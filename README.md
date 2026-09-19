# Paw Pantry

Private single-owner pet profiles, supply estimates, and a public pet-supply catalog. The website is hosted on Render at https://paw-pantry.onrender.com. Production data is stored separately in Neon Postgres so application redeploys do not erase it.

## Run locally

Use Python 3.14 (Render runtime; tests also run on 3.12). Install `requirements.txt`, set `PAW_PANTRY_API_KEY` to a private random value, and run `uvicorn app:app`. Without `DATABASE_URL`, local development uses SQLite. Production must set `DATABASE_URL` to the Neon TLS connection URL. Never commit or share either secret.

Public pages: `/`, `/catalog`, `/guide`, `/guides/{slug}`, `/calculator`, `/about`, `/privacy`, `/terms`, `/docs`, `/openapi.json`. `/health` checks database connectivity. `/ping` returns an uncached 204 for process liveness without touching the database. Pet and product API requests require the `X-API-Key` header; Swagger's Authorize button accepts it. Missing or incorrect credentials return 401.

## Current connector scope

The Muse submission is under review. The API uses one shared owner key: it does **not** isolate unrelated users. Do not distribute that key to end users or enable public pet-profile use until per-user authentication and authorization are implemented.

Supported examples:

- Create or update a pet profile, including allergies and weight.
- Search the starter catalog by species, category, or keyword.
- Track a purchased supply using a purchase date, package amount, daily use, and matching unit.
- Ask how many days remain and when to consider reordering.
- Inspect, replace, or delete an existing tracked supply.
- Get an available retailer link with its disclosure, without placing an order.

There are no scheduled reminders, automatic orders, checkout, delivery tracking, live prices, veterinary recommendations, or automatic ingredient/allergy filtering. An allergy recorded in a profile does not certify any product as suitable. Reorder dates are estimates, including past dates for overdue supplies. Check actual supplies and the current label.

The public site also offers ten original AI-assisted planning guides and a browser-only refill calculator. It makes no hands-on product-testing claims. Calculator entries are not transmitted or saved.

## Catalog maintenance

`data/seed_products.json` contains stable product IDs. Startup updates those managed IDs and inserts new ones in a transaction; it preserves pets, supplies, and other product rows. Never recycle an ID for a different product. Schema changes need a separate migration plan; startup table creation does not migrate existing columns.

`data/catalog_sources.json` records checked manufacturer pages, Amazon ASINs, checked variants, and link provenance. Five older package/variant records are retired from browsing and search but retained in the database for existing supplies; their alternatives have new IDs. 23 Amazon purchase links are currently configured. Two current entries still lack verified Amazon matches, and Chewy approval is pending. Missing links return 409 rather than a placeholder. Use only verified product ASINs with Amazon’s documented simple text link format; do not guess product IDs or advertise approval that has not been received. Confirm Amazon permits the intended Muse placement before enabling affiliate links inside conversations; a website listing does not establish that permission.

## Affiliate link maintenance

Run `python scripts/check_affiliate_links.py` before publishing. It checks Amazon links against their recorded ASIN and the required `pawpantry-20` tag, and Chewy links against approval and dated verification records without opening affiliate URLs or generating clicks. Verify the current product page and variant separately when adding an ASIN. Amazon’s Link Checker confirmed a sample of the documented format tags to this account. SiteStripe copying is not required. The API also rejects Amazon links with an incorrect tag and supplies a public catalog URL for each item. Use the public website as the shopping destination while Muse placement permission is unresolved.

## Tests and deployment

Install `requirements-dev.txt`, then run `python -m pytest tests -q`. Tests use temporary SQLite databases, never production. They cover auth, validation, supply lifecycle and estimates, catalog updates preserving records, public HTML escaping, and retailer link validation. Production Postgres also needs a deployment smoke check.

Render uses `main`, `pip install -r requirements.txt`, and `uvicorn app:app --host 0.0.0.0 --port $PORT`. Keep auto-deploy set to On Commit and the Render GitHub app restricted to this repository. After a push, confirm Render shows the new commit deployed, `/health` succeeds, and the public catalog displays the expected products. The free Render service can sleep; persistent data does not eliminate cold starts.

For credential rotation, the owner must replace `PAW_PANTRY_API_KEY` in Render and every authorized connector configuration using it, then redeploy and verify the old key no longer works. Never paste the key into chat or commit it to GitHub.

## Free hosting availability

Use `/ping` for frequent external availability checks and Render's Health Check Path. Leave `/health` for on-demand database diagnostics: querying it every few seconds prevents Neon's idle compute from suspending. Startup still connects to the database and seeds the catalog before accepting traffic.

A proposed external schedule is one GET to `https://paw-pantry.onrender.com/ping` every five minutes, with no credentials, cookies, browser execution, or affiliate-link visits. The scheduler must be enabled and its execution history verified separately; deploying this endpoint alone does not enable a schedule. Free hosting can still restart or sleep after missed requests, and Render's 750 monthly free instance hours are shared across the workspace. This is a best-effort workaround, not an uptime guarantee or exemption from provider usage rules.

## Activating Chewy after approval

Chewy is currently **in review** (owner confirmed September 19, 2026). No Chewy commissions or tracking links are active. The catalog and private API support both retailers, but Chewy links stay hidden and its link endpoint returns 409 until all checks pass. Product responses include `chewy_link_available`; clients should check it before requesting a Chewy link and always show the returned disclosure. The catalog displays a pending notice while the application is in review.

After approval:

1. Check the actual Impact contract for eligible orders, rates, and permitted placements. Do not assume repeat purchases or Autoship renewals earn commissions. Website approval alone does not authorize placement inside Muse conversations.
2. Generate product links inside Paw Pantry's approved Impact account. Verify each destination against the existing catalog item's exact size, flavor, count, and variant. Do not guess tracking IDs, reuse another publisher's link, or substitute a different variant under an existing product ID.
3. Put the full dashboard-issued URL in the product's `chewy_url` in `data/seed_products.json`. In that product's `data/catalog_sources.json` record, add `chewy_affiliate_url` (the same exact URL), `chewy_product_url` (the verified Chewy product page), `chewy_checked` (date), `chewy_verified_variant`, and `chewy_link_source` (how the link was obtained and verified against this account).
4. Change `data/chewy_program.json` status to `approved`, update `status_checked`, and populate `verified_tracking_hosts` with only the exact hostname(s) observed in those dashboard-issued links. This list is deliberately empty today; no tracking domain or publisher ID has been invented. This file and the provenance records are reviewed configuration, not an automatic check of Impact approval.
5. Run the affiliate checker and tests, then deploy and check the live buttons. Update the pending statement on the About page after approval. The catalog notice disappears automatically when the status changes.

Both retailers use the same stable product record and disclosure behavior. Ordinary Chewy product URLs are not accepted as affiliate links without the approval and exact-link verification records. To disable Chewy links later, set the program status to `paused`; API and catalog both stop serving them even if URLs remain in the database. These changes require no database migration and do not alter pet profiles or supplies.
