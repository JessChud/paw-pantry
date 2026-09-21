import json
import re
import runpy
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]


def test_reviewed_amazon_expansion_is_complete_and_synchronized():
    expansion = json.loads((ROOT / 'data/amazon_product_expansion.json').read_text())
    products = json.loads((ROOT / 'data/seed_products.json').read_text())
    sources = json.loads((ROOT / 'data/catalog_sources.json').read_text())

    assert len(expansion) == 491
    assert len({row[0] for row in expansion}) == 491
    assert all(re.fullmatch(r'[A-Z0-9]{10}', row[0]) for row in expansion)
    assert all(len(row) == 4 and all(isinstance(value, str) and value.strip()
                                     for value in row) for row in expansion)

    expansion_products = [product for product in products if 31 <= product['id'] <= 521]
    assert len(expansion_products) == 491
    for product, (asin, title, species, category) in zip(expansion_products, expansion):
        source = sources[str(product['id'])]
        parsed = urlparse(product['amazon_url'])
        assert product['name'] == title
        assert product['species'] == species
        assert product['category'] == category
        assert f'/dp/{asin}/' in parsed.path
        assert parse_qs(parsed.query)['tag'] == ['pawpantry-20']
        assert source['amazon_asin'] == asin
        assert source['verified_variant'] == title
        assert source['amazon_checked'] == '2026-09-21'
        assert source['catalog_status'] == 'active'


def test_curated_expansion_builder_is_deterministic(tmp_path):
    products_before = (ROOT / 'data/seed_products.json').read_bytes()
    sources_before = (ROOT / 'data/catalog_sources.json').read_bytes()

    namespace = runpy.run_path(str(ROOT / 'scripts/build_curated_product_expansion.py'),
                               run_name='catalog_builder_test')
    namespace['build']()

    assert (ROOT / 'data/seed_products.json').read_bytes() == products_before
    assert (ROOT / 'data/catalog_sources.json').read_bytes() == sources_before
