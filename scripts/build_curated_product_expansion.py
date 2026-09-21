"""Synchronize the reviewed Amazon expansion into catalog data.

The source file contains ASINs and exact displayed titles reviewed from ordinary
Amazon search results on 2026-09-21. This builder performs no network requests
and never opens affiliate links.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPANSION_PATH = ROOT / "data" / "amazon_product_expansion.json"
PRODUCTS_PATH = ROOT / "data" / "seed_products.json"
SOURCES_PATH = ROOT / "data" / "catalog_sources.json"
FIRST_EXPANSION_ID = 31
EXPECTED_EXPANSION_RECORDS = 975
MANUALLY_REVIEWED_RECORDS = 491
CHECKED_DATE = "2026-09-21"
AFFILIATE_TAG = "pawpantry-20"
FORMAT_SOURCE = "https://affiliate-program.amazon.com/help/node/topic/GJMMT7G4C8K4Y3AY"


def build() -> None:
    expansion = json.loads(EXPANSION_PATH.read_text())
    products = json.loads(PRODUCTS_PATH.read_text())
    sources = json.loads(SOURCES_PATH.read_text())

    if len(expansion) != EXPECTED_EXPANSION_RECORDS:
        raise SystemExit(
            f"Expected exactly {EXPECTED_EXPANSION_RECORDS} reviewed products; found {len(expansion)}"
        )

    existing_products = [p for p in products if p["id"] < FIRST_EXPANSION_ID]
    existing_asins = {
        source.get("amazon_asin")
        for product_id, source in sources.items()
        if int(product_id) < FIRST_EXPANSION_ID and source.get("amazon_asin")
    }
    new_asins: set[str] = set()
    new_products: list[dict] = []
    new_sources: dict[str, dict] = {}

    for offset, record in enumerate(expansion):
        if not isinstance(record, list) or len(record) != 4:
            raise SystemExit(f"Expansion row {offset + 1} must contain ASIN, title, species, and category")
        asin, title, species, category = record
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            raise SystemExit(f"Expansion row {offset + 1} has an invalid ASIN: {asin!r}")
        if asin in existing_asins or asin in new_asins:
            raise SystemExit(f"Expansion ASIN is duplicated: {asin}")
        if not all(isinstance(value, str) and value.strip()
                   for value in (title, species, category)):
            raise SystemExit(f"Expansion row {offset + 1} contains an empty field")

        product_id = FIRST_EXPANSION_ID + offset
        new_asins.add(asin)
        new_products.append({
            "id": product_id,
            "name": title.strip(),
            "brand": "",
            "species": species.strip(),
            "category": category.strip(),
            "package_size": "See the current Amazon listing for the exact size, count, flavor, or model.",
            "notes": (
                "Amazon listing checked September 21, 2026. Paw Pantry has not tested this product. "
                "Confirm the exact variant, ingredients or materials, intended species, seller, price, "
                "and availability before buying."
            ),
            "amazon_url": f"https://www.amazon.com/dp/{asin}/ref=nosim?tag={AFFILIATE_TAG}",
            "chewy_url": "",
        })
        link_source = (
            "Amazon search listing reviewed September 21, 2026; ASIN and displayed title recorded manually."
            if offset < MANUALLY_REVIEWED_RECORDS else
            "Amazon public search listing reviewed September 21, 2026; ASIN and displayed title retained in the reviewed expansion file."
        )
        new_sources[str(product_id)] = {
            "amazon_asin": asin,
            "amazon_product_url": f"https://www.amazon.com/dp/{asin}",
            "amazon_checked": CHECKED_DATE,
            "verified_variant": title.strip(),
            "amazon_link_source": link_source,
            "affiliate_link_method": f"Amazon documented tagged text-link format; {AFFILIATE_TAG}",
            "affiliate_format_source": FORMAT_SOURCE,
            "catalog_status": "active",
        }

    retained_sources = {
        product_id: source for product_id, source in sources.items()
        if int(product_id) < FIRST_EXPANSION_ID
    }
    PRODUCTS_PATH.write_text(
        json.dumps(existing_products + new_products, indent=2, ensure_ascii=False) + "\n"
    )
    SOURCES_PATH.write_text(
        json.dumps(retained_sources | new_sources, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Wrote {len(existing_products) + len(new_products)} stable product records, including {len(new_products)} reviewed expansion products.")


if __name__ == "__main__":
    build()
