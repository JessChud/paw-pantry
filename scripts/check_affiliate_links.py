"""Validate catalog link configuration without network requests or affiliate clicks."""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


def check():
    products = json.loads((ROOT / 'data/seed_products.json').read_text())
    metadata = json.loads((ROOT / 'data/catalog_sources.json').read_text())
    errors, linked = [], 0
    ids = [p['id'] for p in products]
    if len(set(ids)) != len(ids):
        errors.append('Duplicate product IDs')
    for product in products:
        url = product.get('amazon_url')
        if not url:
            continue
        linked += 1
        parsed = urlparse(url)
        asin = re.search(r'/dp/([A-Z0-9]{10})(?:/|$)', parsed.path)
        source = metadata.get(str(product['id']), {})
        if (parsed.scheme != 'https' or parsed.hostname != 'www.amazon.com'
                or parsed.username or parsed.password
                or parse_qs(parsed.query).get('tag') != ['pawpantry-20'] or not asin):
            errors.append(f"Product {product['id']}: invalid affiliate destination or tracking tag")
        elif source.get('amazon_asin') != asin.group(1):
            errors.append(f"Product {product['id']}: ASIN differs from checked source")
        if not source.get('amazon_checked') or not source.get('verified_variant'):
            errors.append(f"Product {product['id']}: missing dated variant verification")
        if source.get('catalog_status') == 'retired':
            errors.append(f"Product {product['id']}: retired product still has a purchase link")
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'{linked} Amazon links passed ASIN, variant-record, HTTPS, and tracking-tag checks.')


if __name__ == '__main__':
    check()
