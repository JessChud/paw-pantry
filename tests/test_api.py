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
    monkeypatch.setenv('MUSE_CONNECTOR_API_KEY', 'test-muse-secret')
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


def all_products(client):
    rows = []
    for offset in range(0, 100001, 500):
        page = client.get('/products', params={'limit': 500, 'offset': offset}).json()
        rows.extend(page)
        if len(page) < 500:
            return rows
    return rows


def test_auth_and_schema(api):
    client, _ = api
    assert client.get('/pets', headers={'X-API-Key': ''}).status_code == 401
    assert client.get('/pets', headers={'X-API-Key': 'wrong'}).status_code == 401
    assert client.get('/health').json() == {'ok': True}
    schema = client.get('/openapi.json').json()
    assert schema['info']['title'] == 'Paw Pantry Connector API'
    assert schema['components']['securitySchemes']['APIKeyHeader']['name'] == 'X-API-Key'
    assert set(schema['paths']) == {
        '/health', '/ready', '/products', '/inventory', '/catalog-stats', '/shopping-options',
        '/products/{product_id}/link', '/refill-estimate',
    }
    assert not any(path.startswith('/pets') for path in schema['paths'])
    assert {spec['operationId'] for methods in schema['paths'].values()
            for spec in methods.values()} == {
        'check_service_health', 'check_connector_readiness',
        'search_curated_products', 'search_product_type_inventory',
        'get_catalog_statistics', 'find_shopping_options',
        'get_retailer_link', 'estimate_refill_date',
    }


def test_custom_domain_keeps_submitted_render_api_working(api):
    client, _ = api
    old = 'https://paw-pantry.onrender.com'
    new = 'https://paw-supplies.com'
    response = client.get(old + '/catalog?q=dog%20food', follow_redirects=False)
    assert response.status_code == 308
    assert response.headers['location'] == new + '/catalog?q=dog%20food'
    assert client.head(old + '/', follow_redirects=False).headers['location'] == new + '/'
    assert client.get(new + '/').status_code == 200
    for path in ('/openapi.json', '/docs', '/documentation', '/privacy', '/terms',
                 '/ready', '/shopping-options?q=hamster+food'):
        response = client.get(old + path, follow_redirects=False)
        assert response.status_code == 200, path
        assert 'location' not in response.headers, path
    assert client.get(new + '/shopping-options?q=hamster+food').status_code == 200


def test_connector_key_is_limited_to_stateless_operations(api):
    client, module = api
    connector_headers = {'X-API-Key': 'test-muse-secret'}
    assert client.get('/ready').json() == {
        'ready': True, 'scope': 'stateless-muse-connector'
    }
    assert client.get('/products', headers=connector_headers).status_code == 200
    before = module.SessionLocal().query(module.PetSupply).count()
    bought = date.today() - timedelta(days=2)
    response = client.post('/refill-estimate', headers=connector_headers, json={
        'purchase_date': bought.isoformat(),
        'package_amount': 80,
        'daily_use': 8,
        'unit': 'oz',
        'reorder_lead_days': 3,
    })
    assert response.status_code == 200
    assert response.json()['days_total'] == 10
    assert response.json()['days_left'] == 8
    assert response.json()['runs_out'] == (bought + timedelta(days=10)).isoformat()
    assert module.SessionLocal().query(module.PetSupply).count() == before
    assert client.get('/pets', headers=connector_headers).status_code == 401
    assert client.post('/pets', headers=connector_headers,
                       json={'name': 'Muse pet', 'species': 'dog'}).status_code == 401


def test_owner_and_connector_keys_must_differ(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'same-key.db'))
    monkeypatch.setenv('PAW_PANTRY_API_KEY', 'same-secret')
    monkeypatch.setenv('MUSE_CONNECTOR_API_KEY', 'same-secret')
    sys.modules.pop('app', None)
    with pytest.raises(RuntimeError, match='must differ'):
        importlib.import_module('app')


def test_availability_checks_support_head_and_detect_database_failure(api):
    client, module = api
    client.headers.pop('X-API-Key')
    for path in ('/', '/health'):
        response = client.head(path)
        assert response.status_code == 200
        assert response.content == b''
        assert response.headers['content-type'] == client.get(path).headers['content-type']

    class UnavailableDatabase:
        def execute(self, statement):
            raise module.SQLAlchemyError('private connection failure')

    module.app.dependency_overrides[module.get_db] = lambda: UnavailableDatabase()
    try:
        assert client.head('/health').status_code == 503
        assert client.get('/health').json() == {'detail': 'database unavailable'}
    finally:
        module.app.dependency_overrides.clear()


def test_liveness_probe_never_opens_database(api, monkeypatch):
    client, module = api
    client.headers.pop('X-API-Key')

    def database_must_not_be_opened(*args, **kwargs):
        raise AssertionError('liveness probes must not access the database')

    monkeypatch.setattr(module, 'SessionLocal', database_must_not_be_opened)
    monkeypatch.setattr(module.engine, 'connect', database_must_not_be_opened)
    for method in ('GET', 'HEAD'):
        response = client.request(method, '/ping')
        assert response.status_code == 204
        assert response.content == b''
        assert response.headers['cache-control'] == 'no-store'


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
    sources = json.loads((module.BASE_DIR / 'data/catalog_sources.json').read_text())
    active_count = sum(source.get('catalog_status', 'active') == 'active'
                       for source in sources.values())
    rows[0]['notes'] = 'Updated catalog content'
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/seed_products.json').write_text(json.dumps(rows))
    (tmp_path / 'data/catalog_sources.json').write_text(json.dumps(sources))
    monkeypatch.setattr(module, 'BASE_DIR', tmp_path)
    module.seed()
    module.seed()
    assert client.get('/products').json()[0]['notes'] == 'Updated catalog content'
    products = all_products(client)
    assert len(products) == active_count
    assert client.get(f'/pets/{pet_id}').status_code == 200
    assert len(client.get(f'/pets/{pet_id}/supplies').json()) == 1


def test_link_validation_and_disclosure(api):
    client, module = api
    assert client.get('/products/1/link?retailer=other').status_code == 422
    assert client.get('/products/1/link?retailer=chewy').status_code == 409
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).amazon_url = 'https://evil.example/redirect'
    fallback = client.get('/products/1/link')
    assert fallback.status_code == 200
    assert fallback.json()['kind'] == 'search'
    assert 'tag=pawpantry-20' in fallback.json()['url']
    assert 'evil.example' not in fallback.json()['url']
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).amazon_url = 'https://www.amazon.com/dp/B09K8YYVWV?tag=pawpantry-20'
    response = client.get('/products/1/link').json()
    assert response['destination'] == 'Amazon'
    assert response['button_label'] == 'Check on Amazon'
    assert response['affiliate'] is True
    assert response['opens_after_user_click'] is True
    assert response['rel'] == 'sponsored nofollow noopener'
    assert 'As an Amazon Associate I earn from qualifying purchases.' in response['disclosure']


def test_product_search_returns_display_ready_amazon_option(api):
    client, _ = api
    product = client.get('/products', params={'q': 'Blue Buffalo'}).json()[0]
    amazon = next(option for option in product['retailer_options']
                  if option['retailer'] == 'amazon')
    assert product['amazon_link_available'] is True
    assert amazon['destination'] == 'Amazon'
    assert amazon['button_label'] == 'Check on Amazon'
    assert amazon['url'].startswith('https://www.amazon.com/')
    assert 'tag=pawpantry-20' in amazon['url']
    assert amazon['affiliate'] is True
    assert amazon['opens_after_user_click'] is True
    assert amazon['rel'] == 'sponsored nofollow noopener'
    assert amazon['source_page_url'].endswith('/catalog#product-1')
    assert 'As an Amazon Associate I earn from qualifying purchases.' in amazon['disclosure']

    schema = client.get('/openapi.json').json()
    product_schema = schema['components']['schemas']['CatalogProduct']
    assert 'retailer_options' in product_schema['properties']


def test_open_ended_shopping_search_is_ranked_and_has_broad_amazon_fallback(api):
    client, _ = api
    result = client.get('/shopping-options', params={'q': 'durable chew toy for my puppy'}).json()
    assert result['query'] == 'durable chew toy for my puppy'
    assert result['matching_method'] == 'keyword'
    assert result['semantic_model'] is None
    assert result['curated_products'][0]['species'] == 'dog'
    assert result['curated_products'][0]['category'] == 'toys'
    assert result['curated_products'][0]['verified_amazon_product_link'] is True
    assert result['matched_inventory'][0]['title'] == 'Durable chew toy'
    amazon = result['broader_amazon_search']
    assert amazon['kind'] == 'search'
    assert amazon['button_label'] == 'See more options on Amazon'
    assert amazon['affiliate'] is True
    assert amazon['opens_after_user_click'] is True
    assert 'tag=pawpantry-20' in amazon['url']
    assert 'field-keywords=durable+chew+toy+for+my+puppy+pet+supplies' in amazon['url']
    assert 'As an Amazon Associate I earn from qualifying purchases.' in amazon['disclosure']


def test_product_search_understands_common_pet_language(api):
    client, _ = api
    puppy = client.get('/products', params={'q': 'puppy chew toy'}).json()
    assert puppy[0]['species'] == 'dog'
    assert puppy[0]['category'] == 'toys'
    assert puppy[0]['verified_amazon_product_link'] is True
    hungry_cat = client.get('/products', params={'q': 'my kitten is hungry'}).json()
    assert hungry_cat
    assert hungry_cat[0]['species'] == 'cat'
    assert hungry_cat[0]['category'] == 'food'
    assert client.get('/shopping-options', params={'q': 'x'}).status_code == 422
    assert client.get('/shopping-options', params={'q': 'x' * 201}).status_code == 422


@pytest.mark.parametrize(
    ('query', 'species', 'product_category', 'intent_category', 'corrected_term', 'typo'), [
    ('pet hampster', 'hamster', None, None, 'hamster', 'hampster'),
    ('kittten litter', 'cat', 'litter', 'litter', 'kitten', 'kittten'),
    ('aquariam filter', 'fish', 'maintenance', 'habitat', 'aquarium', 'aquariam'),
    ('rabitt hay', 'rabbit', 'food', 'food', 'rabbit', 'rabitt'),
    ('guiena pig food', 'guinea-pig', 'food', 'food', 'guinea', 'guiena'),
])
def test_search_corrects_common_pet_and_supply_typos(
        api, query, species, product_category, intent_category, corrected_term, typo):
    client, _ = api
    result = client.get('/shopping-options', params={'q': query, 'limit': 3}).json()
    assert result['curated_products'][0]['species'] == species
    assert result['matched_inventory'][0]['species'] == species
    if product_category:
        assert result['curated_products'][0]['category'] == product_category
    if intent_category:
        assert result['matched_inventory'][0]['category'] == intent_category
    amazon_url = result['broader_amazon_search']['url']
    assert result['interpreted_query'] == query.replace(typo, corrected_term)
    assert corrected_term in amazon_url
    assert typo not in amazon_url


def test_public_tester_shows_spelling_interpretation(api):
    client, _ = api
    client.headers.pop('X-API-Key')
    response = client.get('/tester', params={'q': 'pet hampster'})
    assert response.status_code == 200
    assert 'Interpreted as “pet hamster”.' in response.text
    assert '&quot;interpreted_query&quot;: &quot;pet hamster&quot;' in response.text


def test_public_connector_tester_uses_real_results_without_exposing_credentials(api):
    client, _ = api
    client.headers.pop('X-API-Key')
    empty = client.get('/tester')
    assert empty.status_code == 200
    assert 'Test the connector' in empty.text
    response = client.get('/tester', params={'q': 'durable chew toy for my puppy'})
    assert response.status_code == 200
    assert 'curated matches' in response.text
    assert '&quot;curated_products&quot;' in response.text
    assert 'tag=pawpantry-20' in response.text
    assert 'As an Amazon Associate I earn from qualifying purchases.' in response.text
    assert 'test-muse-secret' not in response.text
    assert '/tester' not in client.get('/openapi.json').json()['paths']


@pytest.mark.parametrize(('query', 'expected_product_species', 'expected_intent'), [
    ('terrarium substrate for leopard gecko', 'reptile', 'Reptile substrate'),
    ('hay for my rabbit', 'rabbit', 'Timothy hay'),
    ('cage cleaner for my bird', 'bird', 'Cage cleaning brush'),
    ('indestructible toy for a power chewer', 'dog', 'Durable chew toy'),
])
def test_open_ended_search_ranks_species_and_category_signals(
        api, query, expected_product_species, expected_intent):
    client, _ = api
    result = client.get('/shopping-options', params={'q': query}).json()
    assert result['curated_products'][0]['species'] == expected_product_species
    assert result['matched_inventory'][0]['title'] == expected_intent
    if 'substrate' in query:
        assert 'substrate' in result['curated_products'][0]['name'].lower()


@pytest.mark.parametrize(('query', 'expected_fragment'), [
    ('senior cat food', 'Senior'),
    ('freeze-dried cat treats', 'Freeze Dried'),
])
def test_new_catalog_gaps_surface_exact_product_types(api, query, expected_fragment):
    client, _ = api
    result = client.get('/shopping-options', params={'q': query}).json()
    assert expected_fragment in result['curated_products'][0]['name']
    assert result['curated_products'][0]['verified_amazon_product_link'] is True
    assert all(product['species'] == 'cat' for product in result['curated_products'])


@pytest.mark.parametrize(('query', 'species', 'category'), [
    ('orthopedic dog bed', 'dog', 'beds'),
    ('cat water fountain', 'cat', 'feeding'),
    ('tropical fish food', 'fish', 'food'),
    ('parakeet pellet food', 'bird', 'food'),
    ('rabbit pellets', 'rabbit', 'food'),
    ('hamster exercise wheel', 'hamster', 'toys'),
    ('ferret litter pan', 'ferret', 'litter'),
    ('reptile hide cave', 'reptile', 'habitat'),
    ('turtle basking platform', 'turtle', 'habitat'),
    ('chinchilla dust bath', 'chinchilla', 'grooming'),
    ('hedgehog food', 'hedgehog', 'food'),
    ('hermit crab salt water conditioner', 'hermit-crab', 'water-care'),
    ('snake hide cave', 'snake', 'habitat'),
    ('bearded dragon food', 'lizard', 'food'),
    ('rat enrichment toy', 'rat', 'toys'),
    ('gerbil food', 'gerbil', 'food'),
    ('mouse habitat', 'mouse', 'habitat'),
    ('frog food', 'amphibian', 'food'),
    ('dog joint supplement', 'dog', 'supplements'),
    ('cat tree', 'cat', 'furniture'),
    ('aquarium filter', 'fish', 'maintenance'),
    ('reptile UVB lamp', 'reptile', 'heating-lighting'),
    ('puppy training pads', 'dog', 'training'),
    ('dog life jacket', 'dog', 'safety'),
    ('cat dental care', 'cat', 'dental'),
])
def test_expanded_catalog_surfaces_diverse_exact_products(api, query, species, category):
    client, _ = api
    result = client.get('/shopping-options', params={'q': query, 'limit': 3}).json()
    top = result['curated_products'][0]
    assert (top['species'], top['category']) == (species, category)
    assert top['verified_amazon_product_link'] is True


def test_catalog_stats_are_honest_and_public(api):
    client, module = api
    client.headers.pop('X-API-Key')
    module.catalog_metadata.cache_clear()
    stats = client.get('/catalog-stats').json()
    assert stats['active_curated_products'] == 4000
    assert stats['retired_products'] == 5
    assert stats['shopping_intents'] == 2771
    assert stats['verified_amazon_products'] == 3998
    assert stats['affiliate_enabled_active_products'] == 4000
    assert stats['affiliate_enabled_intents'] == 2771
    assert stats['verified_chewy_products'] == 0
    assert stats['species_counts']['dog'] == 776
    assert stats['species_counts']['cat'] == 658
    assert stats['species_counts']['fish'] == 328
    assert stats['species_counts']['bird'] == 276
    assert stats['species_counts']['ferret'] == 152
    assert stats['species_counts']['hermit-crab'] == 114
    assert stats['species_counts']['gerbil'] == 86
    assert stats['species_counts']['mouse'] == 98
    assert stats['species_counts']['amphibian'] == 88
    assert stats['category_counts']['food'] == 827
    assert stats['category_counts']['toys'] == 341
    assert stats['category_counts']['habitat'] == 614
    assert stats['category_counts']['supplements'] == 191
    assert stats['category_counts']['heating-lighting'] == 149
    assert stats['shopping_species_counts']['dog'] == 473
    assert stats['shopping_species_counts']['guinea-pig'] == 66
    assert stats['shopping_species_counts']['ferret'] == 159
    assert stats['shopping_category_counts']['food'] == 384
    assert stats['broader_amazon_search_enabled'] is True
    assert stats['first_party_recommendation_pages'] == 2771
    assert stats['semantic_search_enabled'] is False
    assert stats['semantic_model'] is None
    assert module.catalog_metadata.cache_info().misses == 1


@pytest.mark.parametrize(('query', 'expected'), [
    ('airline approved carrier for my cat', 'Cat carrier'),
    ('self cleaning litter box', 'Self-cleaning litter box'),
    ('water conditioner for my aquarium', 'Water conditioner'),
    ('exercise wheel for my hamster', 'Hamster exercise wheel'),
    ('hay for my guinea pig', 'Guinea pig hay'),
    ('hands free leash for running', 'Hands-free running leash'),
    ('soft sided ferret carrier', 'Ferret carrier — soft-sided option'),
])
def test_expanded_inventory_matches_common_requests(api, query, expected):
    client, _ = api
    response = client.get('/inventory', params={'q': query, 'limit': 5})
    assert response.status_code == 200
    assert response.json()[0]['title'] == expected


def test_inventory_is_product_type_coverage_not_fake_retail_stock(api):
    client, _ = api
    result = client.get('/inventory', params={
        'species': 'reptile', 'category': 'habitat', 'limit': 50,
    }).json()
    assert result
    assert all(row['species'] in {'reptile', 'any'} for row in result)
    assert all(row['category'] == 'habitat' for row in result)
    assert all('url' not in row and 'price' not in row for row in result)
    assert all(row['website_url'].startswith('https://paw-supplies.com/shop/')
               for row in result)
    assert all(row['retailer_options'][0]['kind'] == 'search' for row in result)
    assert all('tag=pawpantry-20' in row['retailer_options'][0]['url'] for row in result)


def test_every_active_product_and_inventory_concept_has_affiliate_path(api):
    client, _ = api
    products = all_products(client)
    assert len(products) == 4000
    assert all(product['amazon_link_available'] for product in products)
    assert sum(product['verified_amazon_product_link'] for product in products) == 3998
    assert sum(product['affiliate_search_available'] for product in products) == 2
    assert all(any(option['retailer'] == 'amazon' and option['affiliate']
                   for option in product['retailer_options']) for product in products)

    inventory = client.get('/inventory', params={'limit': 100}).json()
    assert len(inventory) == 100
    assert all(row['retailer_options'][0]['affiliate'] for row in inventory)
    assert all(row['retailer_options'][0]['opens_after_user_click'] for row in inventory)


def test_catalog_and_inventory_support_stable_pagination(api):
    client, _ = api
    first_products = client.get('/products', params={'limit': 7, 'offset': 0}).json()
    second_products = client.get('/products', params={'limit': 7, 'offset': 7}).json()
    assert len(first_products) == len(second_products) == 7
    assert {row['id'] for row in first_products}.isdisjoint(
        row['id'] for row in second_products)

    first_inventory = client.get('/inventory', params={'limit': 11, 'offset': 0}).json()
    second_inventory = client.get('/inventory', params={'limit': 11, 'offset': 11}).json()
    assert len(first_inventory) == len(second_inventory) == 11
    assert {row['id'] for row in first_inventory}.isdisjoint(
        row['id'] for row in second_inventory)
    assert client.get('/products', params={'offset': -1}).status_code == 422
    assert client.get('/inventory', params={'offset': 10001}).json() == []
    assert client.get('/products', params={'offset': 10001}).json() == []
    assert client.get('/catalog', params={'offset': -1}).status_code == 422
    first_catalog = client.get('/catalog')
    second_catalog = client.get('/catalog', params={'offset': 48})
    assert first_catalog.text.count('<article id="product-') == 48
    assert second_catalog.text.count('<article id="product-') == 48
    assert 'offset=48' in first_catalog.text
    assert 'offset=0' in second_catalog.text


def test_recommendation_library_and_first_party_source_page(api):
    client, _ = api
    client.headers.pop('X-API-Key')
    library = client.get('/recommendations', params={'q': 'ferret hammock'})
    assert library.status_code == 200
    assert '2,771 product-type' in library.text
    assert '/shop/ferret-ferret-hammock' in library.text
    page = client.get('/shop/ferret-ferret-hammock')
    assert page.status_code == 200
    assert 'Ferret hammock' in page.text
    assert 'tag=pawpantry-20' in page.text
    assert 'As an Amazon Associate I earn from qualifying purchases.' in page.text
    assert client.get('/shop/not-a-real-intent').status_code == 404


def test_semantic_ranking_can_retrieve_without_keyword_overlap(api, monkeypatch):
    client, module = api

    class FakeSemanticRanker:
        enabled = True
        model = 'test-small-embedding'

        def rank(self, query, item_texts):
            return {item_id: (1.0 if item_id == 17 else 0.1) for item_id in item_texts}

    monkeypatch.setattr(module, 'SEMANTIC_RANKER', FakeSemanticRanker())
    result = client.get('/shopping-options', params={'q': 'something for my aquatic friend'}).json()
    assert result['matching_method'] == 'hybrid-semantic'
    assert result['semantic_model'] == 'test-small-embedding'
    assert result['curated_products'][0]['id'] == 17


def test_semantic_product_reranking_has_bounded_request_size(api, monkeypatch):
    client, module = api
    seen = []

    class RecordingRanker:
        enabled = True
        model = 'test-small-embedding'

        def rank(self, query, item_texts):
            seen.append(len(item_texts))
            return None

    monkeypatch.setattr(module, 'SEMANTIC_RANKER', RecordingRanker())
    result = client.get('/products', params={'q': 'dog food', 'limit': 5})
    assert result.status_code == 200
    assert seen == [128]


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


def test_catalog_labels_and_blank_brands_render_cleanly(api):
    client, _ = api
    client.headers.pop('X-API-Key')
    page = client.get('/catalog', params={'q': 'ORIJEN'})
    assert page.status_code == 200
    assert '<h2> ORIJEN' not in page.text
    assert '<h2>ORIJEN' in page.text
    assert '>Guinea Pig</option>' in page.text
    assert '>Water Care</option>' in page.text


def test_wrong_tracking_tag_is_not_published(api):
    client, module = api
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).amazon_url = 'https://www.amazon.com/dp/B09K8YYVWV?tag=wrong-owner-20'
    fallback = client.get('/products/1/link').json()
    assert fallback['kind'] == 'search'
    assert 'tag=pawpantry-20' in fallback['url']
    product = next(row for row in client.get('/products').json() if row['id'] == 1)
    assert product['amazon_link_available'] is True
    assert product['verified_amazon_product_link'] is False
    assert product['affiliate_search_available'] is True
    assert any(option['retailer'] == 'amazon' and option['kind'] == 'search'
               for option in product['retailer_options'])
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
    assert tracked['chewy_link_available'] is False
    assert tracked['retailer_options'] == []


def reviewed_chewy_fixture(module, monkeypatch):
    # Synthetic tracker for local tests only; never added to production data.
    url = 'https://tracking.example/paw-pantry/product-1?variant=5lb&source=website'
    sources = module.catalog_metadata()
    sources['1'].update({
        'chewy_affiliate_url': url,
        'chewy_product_url': 'https://www.chewy.com/test-product/dp/123',
        'chewy_checked': '2026-09-19',
        'chewy_verified_variant': 'Chicken & Brown Rice; 5 lb bag',
        'chewy_link_source': 'Synthetic dashboard record for tests only',
    })
    program = {'status': 'approved', 'verified_tracking_hosts': ['tracking.example']}
    monkeypatch.setattr(module, 'catalog_metadata', lambda: sources)
    monkeypatch.setattr(module, 'chewy_program', lambda: program)
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).chewy_url = url
    return url, sources['1'], program


def test_chewy_pending_cannot_publish_even_with_a_link(api, monkeypatch):
    client, module = api
    url, _, program = reviewed_chewy_fixture(module, monkeypatch)
    for status in ('in_review', 'paused', 'rejected', None):
        program['status'] = status
        assert client.get('/products/1/link?retailer=chewy').status_code == 409
        assert client.get('/products').json()[0]['chewy_link_available'] is False
        page = client.get('/catalog').text
        assert 'tracking.example' not in page
        assert 'Check on Amazon' in page
        assert 'Chewy links are coming soon' not in page


def test_verified_chewy_link_is_consistent_in_api_and_catalog(api, monkeypatch):
    client, module = api
    url, _, _ = reviewed_chewy_fixture(module, monkeypatch)
    response = client.get('/products/1/link?retailer=chewy')
    assert response.status_code == 200
    assert response.json()['url'] == url
    assert response.json()['destination'] == 'Chewy'
    assert 'commission' in response.json()['disclosure']
    assert 'Amazon Associate' not in response.json()['disclosure']
    assert response.json()['product']['chewy_link_available'] is True
    chewy = next(option for option in response.json()['product']['retailer_options']
                 if option['retailer'] == 'chewy')
    assert chewy['url'] == url
    assert chewy['button_label'] == 'Check on Chewy'
    page = client.get('/catalog').text
    assert 'Check on Chewy' in page
    assert 'Affiliate link — Paw Pantry may earn a commission.' in page
    assert 'variant=5lb&amp;source=website' in page
    assert 'Chewy links are coming soon' not in page
    client.headers.pop('X-API-Key')
    assert client.get('/catalog').status_code == 200
    assert client.get('/products/1/link?retailer=chewy').status_code == 401


@pytest.mark.parametrize('bad_url', [
    'https://www.chewy.com/test-product/dp/123',
    'https://tracking.example/another-publisher/product-1',
    'https://tracking.example.evil.test/product-1',
    'https://user:password@tracking.example/product-1',
    'http://tracking.example/product-1',
    'https://tracking.example:8443/product-1',
    'https://[broken',
])
def test_unverified_chewy_links_never_publish(api, monkeypatch, bad_url):
    client, module = api
    reviewed_chewy_fixture(module, monkeypatch)
    with module.SessionLocal.begin() as db:
        db.get(module.Product, 1).chewy_url = bad_url
    assert client.get('/products/1/link?retailer=chewy').status_code == 409
    assert client.get('/products').json()[0]['chewy_link_available'] is False
    assert 'Check on Chewy' not in client.get('/catalog').text


def test_chewy_requires_variant_record_and_blocks_retired_products(api, monkeypatch):
    client, module = api
    url, source, program = reviewed_chewy_fixture(module, monkeypatch)
    for field in ('chewy_affiliate_url', 'chewy_product_url', 'chewy_checked',
                  'chewy_verified_variant', 'chewy_link_source'):
        saved = source.pop(field)
        assert client.get('/products/1/link?retailer=chewy').status_code == 409
        source[field] = saved
    source['catalog_status'] = 'retired'
    assert client.get('/products/1/link?retailer=chewy').status_code == 409
    assert client.get('/products/1/link?retailer=amazon').status_code == 409
    assert 'tracking.example' not in client.get('/catalog').text
