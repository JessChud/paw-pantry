import importlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db'))
    monkeypatch.setenv('PAW_PANTRY_API_KEY', 'test-only-secret')
    sys.path.insert(0, str(Path(__file__).parents[1]))
    sys.modules.pop('app', None)
    module = importlib.import_module('app')
    with TestClient(module.app) as client:
        client.headers['X-API-Key'] = 'test-only-secret'
        yield client, module


def pet(client):
    response = client.post('/pets', json={'name': 'Luna', 'species': 'Cat'})
    assert response.status_code == 200
    return response.json()['id']


def supply_payload(**overrides):
    return {'product_id': 1, 'package_amount': 10, 'daily_use': 3,
            'last_purchased': date.today().isoformat(), **overrides}


def test_auth_and_schema(api):
    client, _ = api
    assert client.get('/pets', headers={'X-API-Key': ''}).status_code == 401
    assert client.get('/pets', headers={'X-API-Key': 'wrong'}).status_code == 401
    assert client.get('/health').json() == {'ok': True}
    schema = client.get('/openapi.json').json()
    assert schema['components']['securitySchemes']['APIKeyHeader']['name'] == 'X-API-Key'


def test_input_validation_and_null_update(api):
    client, _ = api
    pet_id = pet(client)
    assert client.get(f'/pets/{pet_id}').json()['species'] == 'cat'
    for body in ({'name': None}, {'name': '  '}, {'weight_lb': -1}, {'bogus': 'x'}):
        assert client.patch(f'/pets/{pet_id}', json=body).status_code == 422
    for changes in ({'daily_use': 0}, {'package_amount': -1},
                    {'last_purchased': (date.today() + timedelta(days=1)).isoformat()},
                    {'last_purchased': '0001-01-01'},
                    {'package_amount': 1000000, 'daily_use': 0.000001}):
        assert client.post(f'/pets/{pet_id}/supplies', json=supply_payload(**changes)).status_code == 422


def test_predictions_preserve_fractional_and_overdue_dates(api):
    client, _ = api
    pet_id = pet(client)
    bought = date.today() - timedelta(days=10)
    response = client.post(f'/pets/{pet_id}/supplies', json=supply_payload(last_purchased=bought.isoformat()))
    assert response.status_code == 200
    prediction = client.get(f'/pets/{pet_id}/runout').json()[0]
    assert prediction['runs_out'] == (bought + timedelta(days=4)).isoformat()
    assert prediction['reorder_by'] == (bought - timedelta(days=1)).isoformat()
    assert prediction['days_left'] == -6.7
    assert prediction['overdue'] is True


def test_supply_lifecycle_and_pet_boundaries(api):
    client, _ = api
    first, second = pet(client), pet(client)
    supply_id = client.post(f'/pets/{first}/supplies', json=supply_payload()).json()['id']
    assert client.put(f'/pets/{second}/supplies/{supply_id}', json=supply_payload()).status_code == 404
    assert client.delete(f'/pets/{second}/supplies/{supply_id}').status_code == 404
    assert client.put(f'/pets/{first}/supplies/{supply_id}', json=supply_payload(package_amount=30)).status_code == 200
    assert len(client.get(f'/pets/{first}/supplies').json()) == 1
    assert client.get(f'/pets/{first}/runout').json()[0]['days_left'] == 10
    assert client.delete(f'/pets/{first}/supplies/{supply_id}').status_code == 200
    assert client.get(f'/pets/{first}/runout').json() == []


def test_catalog_upsert_preserves_profile_and_supply(api, tmp_path, monkeypatch):
    client, module = api
    pet_id = pet(client)
    client.post(f'/pets/{pet_id}/supplies', json=supply_payload())
    rows = json.loads((module.BASE_DIR / 'data/seed_products.json').read_text())
    rows[0]['notes'] = 'Updated catalog content'
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/seed_products.json').write_text(json.dumps(rows))
    monkeypatch.setattr(module, 'BASE_DIR', tmp_path)
    module.seed()
    module.seed()
    assert client.get('/products').json()[0]['notes'] == 'Updated catalog content'
    assert len(client.get('/products').json()) == len(rows)
    assert client.get(f'/pets/{pet_id}').status_code == 200
    assert len(client.get(f'/pets/{pet_id}/supplies').json()) == 1


def test_link_validation_and_disclosure(api):
    client, module = api
    assert client.get('/products/1/link?retailer=other').status_code == 422
    assert client.get('/products/1/link?retailer=chewy').status_code == 409
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).amazon_url = 'https://evil.example/redirect'
    assert client.get('/products/1/link').status_code == 409
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).amazon_url = 'https://www.amazon.com/dp/B09K8YYVWV?tag=pawpantry-20'
    response = client.get('/products/1/link').json()
    assert response['destination'] == 'Amazon'
    assert 'As an Amazon Associate I earn from qualifying purchases.' in response['disclosure']


def test_default_purchase_date_and_restart_preserve_records(api):
    client, module = api
    pet_id = pet(client)
    payload = supply_payload()
    payload.pop('last_purchased')
    assert client.post(f'/pets/{pet_id}/supplies', json=payload).status_code == 200
    module.engine.dispose()
    module.seed()
    records = client.get(f'/pets/{pet_id}/supplies').json()
    assert records[0]['last_purchased'] == date.today().isoformat()
    assert records[0]['package_amount'] == 10
    assert client.get(f'/pets/{pet_id}').json()['name'] == 'Luna'


def test_public_catalog_filters_and_escapes(api):
    client, module = api
    client.headers.pop('X-API-Key')
    response = client.get('/catalog', params={'species': 'fish'})
    assert response.status_code == 200
    assert 'TetraMin Tropical Flakes' in response.text
    assert 'Blue Buffalo' not in response.text
    response = client.get('/catalog', params={'q': '"<script>alert(1)</script>{{PRODUCTS}}'})
    assert '<script>' not in response.text
    assert '&lt;script&gt;' in response.text
    assert '{{PRODUCTS}}' in response.text
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).notes = '<img src=x onerror=alert(1)>'
    response = client.get('/catalog')
    assert '<img src=x' not in response.text
    assert '&lt;img src=x' in response.text
    assert 'As an Amazon Associate I earn from qualifying purchases.' in response.text
    assert 'tag=pawpantry-20' in response.text


def test_wrong_tracking_tag_is_not_published(api):
    client, module = api
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).amazon_url = 'https://www.amazon.com/dp/B09K8YYVWV?tag=wrong-owner-20'
    assert client.get('/products/1/link').status_code == 409
    assert 'wrong-owner-20' not in client.get('/catalog').text


def test_retired_variants_keep_supply_history(api):
    client, _ = api
    pet_id = pet(client)
    assert client.post(f'/pets/{pet_id}/supplies', json=supply_payload(product_id=3)).status_code == 200
    assert 3 not in [product['id'] for product in client.get('/products').json()]
    assert 'product-3"' not in client.get('/catalog').text
    tracked = client.get(f'/pets/{pet_id}/runout').json()[0]['product']
    assert tracked['id'] == 3
    assert tracked['catalog_status'] == 'retired'
    assert tracked['replacement_product_id'] == 26
    assert tracked['website_url'] is None
    assert tracked['amazon_link_available'] is False
