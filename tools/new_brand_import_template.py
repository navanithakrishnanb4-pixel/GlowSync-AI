"""Scaffold a blank, schema-valid supplier import file for a brand that has no
public feed configured (feed_base is null in data/brands.json).

This does NOT invent any product data. It writes a single placeholder
product with obvious TODO markers so a contributor fills in real,
verifiably-sourced facts (name, official product URL, image URL, price,
availability, observed_at) before running:

    .venv/bin/python tools/catalogue.py import path/to/file.json

Usage:
    .venv/bin/python tools/new_brand_import_template.py <brand-id> [out.json]

Example:
    .venv/bin/python tools/new_brand_import_template.py colorbar data/colorbar-import.json
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.catalogue import BY_BRAND, CATEGORIES  # noqa: E402


def build_template(brand_id: str) -> dict:
    brand = BY_BRAND.get(brand_id)
    if not brand:
        raise SystemExit(
            f"Unknown brand id '{brand_id}'. Add it to data/brands.json first, "
            f"or pick one of: {', '.join(sorted(BY_BRAND))}"
        )
    if brand.get("feed_base"):
        raise SystemExit(
            f"'{brand_id}' already has a public feed configured (feed_base is set). "
            "Use 'tools/catalogue.py sync --brand "
            f"{brand_id}' instead of a supplier import."
        )
    host = brand["allowed_hosts"][0]
    return {
        "schema_version": 1,
        "products": [
            {
                "id": f"{brand_id}:TODO-STABLE-SLUG",
                "brand": brand_id,
                "name": "TODO: exact product name from the official listing",
                "category": "TODO: one of " + ", ".join(sorted(CATEGORIES)),
                "url": f"https://{host}/TODO-official-product-page",
                "image": f"https://{host}/TODO-official-image.jpg",
                "source_url": f"https://{host}/TODO-page-you-observed-this-on",
                "observed_at": int(time.time()),
                "variants": [
                    {
                        "id": "TODO-variant-id-or-shade-slug",
                        "name": "TODO: published shade or variant name",
                        "price_inr": None,
                        "mrp_inr": None,
                        "available": None,
                        "url": f"https://{host}/TODO-official-product-page",
                    }
                ],
            }
        ],
    }


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    brand_id = sys.argv[1]
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / f"data/{brand_id}-import.json"
    out_path.write_text(json.dumps(build_template(brand_id), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote template: {out_path}")
    print("Fill in every TODO with real, verifiable facts from the brand's official site,")
    print("then run: .venv/bin/python tools/catalogue.py import " + str(out_path))


if __name__ == "__main__":
    main()