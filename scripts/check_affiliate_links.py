"""Validate catalog link configuration without network requests or affiliate clicks."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from affiliate_links import valid_chewy_link


def check():
    products = json.loads((ROOT / 'data/seed_products.json').read_text())
    intents = json.loads((ROOT / 'data/shopping_intents.json').read_text())
    metadata = json.loads((ROOT / 'data/catalog_sources.json').read_text())
    program = json.loads((ROOT / 'data/chewy_program.json').read_text())
    errors, linked = [], 0
    chewy_linked = 0
    ids = [p['id'] for p in products]
    if len(set(ids)) != len(ids):
        errors.append('Duplicate product IDs')
    intent_ids = [intent.get('id') for intent in intents]
    if len(set(intent_ids)) != len(intent_ids):
        errors.append('Duplicate shopping-intent IDs')
    if len(intents) < 2000:
        errors.append('Shopping-intent expansion unexpectedly contains fewer than 2,000 records')
    for intent in intents:
        for field in ('id', 'title', 'species', 'category', 'keywords', 'guidance',
                      'base_intent_id'):
            if not intent.get(field):
                errors.append(f"Shopping intent {intent.get('id')!r}: missing {field}")
        if not isinstance(intent.get('keywords'), list):
            errors.append(f"Shopping intent {intent.get('id')!r}: keywords must be a list")
    for product in products:
        if product.get('chewy_url'):
            chewy_linked += 1
            if not valid_chewy_link(product['chewy_url'], metadata.get(str(product['id']), {}), program):
                errors.append(f"Product {product['id']}: Chewy approval or exact-link/variant verification missing or invalid")
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
    active = sum(metadata.get(str(product['id']), {}).get('catalog_status') != 'retired'
                 for product in products)
    print(f'{active} active catalog products have an exact product link or a generated tagged search fallback.')
    print(f'{len(intents)} shopping intents have generated tagged Amazon searches and first-party source pages.')
    print(f'{chewy_linked} Chewy links passed approval, exact-link, HTTPS, and variant-record checks. Program status: {program.get("status", "unconfigured")}.')


if __name__ == '__main__':
    check()
