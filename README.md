# Paw Pantry

Private single-owner pet profiles, supply estimates, and a public pet-supply catalog. The website is hosted on Render at https://paw-pantry.onrender.com. Production data is stored separately in Neon Postgres so application redeploys do not erase it.

## Run locally

Use Python 3.14 (Render runtime; tests also run on 3.12). Install `requirements.txt`, set `PAW_PANTRY_API_KEY` and `MUSE_CONNECTOR_API_KEY` to different private random values, and run `uvicorn app:app`. Without `DATABASE_URL`, local development uses SQLite. Production must set `DATABASE_URL` to the Neon TLS connection URL. Never commit or share these secrets.

Public pages: `/`, `/catalog`, `/recommendations`, `/shop/{intent_id}`, `/guide`, `/guides/{slug}`, `/calculator`, `/about`, `/privacy`, `/terms`, `/documentation`, `/docs`, `/openapi.json`. `/health` checks database connectivity, `/ready` confirms the connector credential and database are configured, and `/ping` returns an uncached 204 for process liveness without touching the database. API requests use the `X-API-Key` header. Missing or incorrect credentials return 401.

## Current connector scope

The published Muse contract is stateless and excludes every private pet-profile and supply-record route. Muse receives `MUSE_CONNECTOR_API_KEY`, which can call only catalog search, shopping options, retailer links, and the stateless refill estimator. The separate `PAW_PANTRY_API_KEY` remains required for all stored pet and supply operations. Startup fails if the two keys are equal.

Muse users therefore cannot overwrite one another's Paw Pantry data: the published connector has no stored user record to create or mutate. Muse supplies current-request context to the stateless tools. Any future connector feature that stores profiles, reminders, or preferences must add end-user OAuth or an equivalent verified user identity plus tenant-scoped database queries before its write routes can enter the published OpenAPI contract.

Supported examples:

- Search the starter catalog by species, category, or keyword. Each result includes
  validated `retailer_options` that Muse can render as direct external buttons with
  the supplied affiliate disclosure.
- Match requests against 2,771 product-type and shopping-constraint records spanning
  20 pet types. These records improve request understanding but are not
  represented as live retailer inventory, tested products, or suitability guarantees.
- Give every active curated product and every shopping-intent record a tagged Amazon
  path. The 3,998 checked ASIN links remain identified as verified product links; the
  other paths are labeled as changing Amazon searches, never exact products.
- Handle open-ended requests through `/shopping-options`. It returns ranked curated
  matches plus a tagged Amazon search-results action for broader choice. The search
  action is clearly identified as changing retailer results rather than a verified
  individual product recommendation.
- Calculate how many days remain and when to consider reordering without storing the inputs.
- Get an available retailer link with its disclosure, without placing an order.

There are no scheduled reminders, automatic orders, checkout, delivery tracking, live prices, veterinary recommendations, or automatic ingredient/allergy filtering. An allergy recorded in a profile does not certify any product as suitable. Reorder dates are estimates, including past dates for overdue supplies. Check actual supplies and the current label.

The checked local product catalog is deliberately smaller than Amazon's catalog. Paw Pantry
uses relevant Amazon pet-supply search actions for long-tail shopping requests. Every
shopping-intent record also has a public `/shop/{intent_id}` source page with original
selection guidance and a deliberate affiliate click. The
Amazon Associates Link Checker validated a representative generated search URL as
tagging to this account. The
intended next phase is Amazon's Creators API `SearchItems` operation, which can return
live product records and vended affiliate URLs. The account's Creators API page
currently requires an approved Associates account and says Product Advertising access
also requires at least 10 qualifying sales within the past 30 days. Creators API
credentials must stay in Render environment variables; they must never be placed in
the repository, browser code, or connector response.

The public site also offers ten original AI-assisted planning guides and a browser-only refill calculator. It makes no hands-on product-testing claims. Calculator entries are not transmitted or saved.

## Search and inventory

`/catalog-stats` reports the current inventory without authentication. The curated
seed has 4,005 stable records: 4,000 active products, five retained retired variants,
3,998 active checked Amazon product links, and no active Chewy links. The curated set
covers 20 species groupings and 24 supply categories. A separate 2,771-record shopping-intent
library covers 20 pet types across 23 categories. `/inventory` searches that coverage
library, while `/shopping-options` combines it with curated products and tagged Amazon
searches. `/recommendations` makes the full library browsable on Paw Pantry, and each
`/shop/{intent_id}` page supplies relevant original guidance before the retailer action.
`/products` and `/inventory` accept `limit` and `offset`, so a client can enumerate
the growing catalogs without dropping records. Connector operations use concise,
stable OpenAPI operation IDs so Muse can select tools reliably across deployments.

Every search first runs through a local pet-shopping normalizer. It corrects
high-confidence misspellings such as `hampster`, `kittten`, and `aquariam`, then
applies species, category, product-type, and title signals. This step has no API
cost and runs before either keyword or optional semantic ranking. The corrected
wording is also used for broader retailer searches instead of forwarding the typo.

When `OPENAI_API_KEY` is set, product and shopping-intent search uses
`text-embedding-3-small` with 256-dimensional embeddings to combine semantic relevance
with the offline keyword score. Catalog vectors are cached in memory; each request
embeds only the query after the first pass for each catalog. If the API is unavailable
or the key is absent, matching falls back automatically to the tested offline ranker.
Optional settings are
`OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMENSIONS`, and
`OPENAI_EMBEDDING_TIMEOUT_SECONDS`. Never commit the API key.

## Catalog maintenance

`data/seed_products.json` contains stable product IDs. Startup updates those managed IDs and inserts new ones in a transaction; it preserves pets, supplies, and other product rows. Never recycle an ID for a different product. Schema changes need a separate migration plan; startup table creation does not migrate existing columns.

`data/catalog_sources.json` records source pages, Amazon ASINs, checked variants, and link provenance. Five older package/variant records are retired from browsing and search but retained in the database for existing supplies; their alternatives have new IDs. 3,998 checked Amazon product links are currently configured. Two active entries lack checked ASIN links and therefore use clearly labeled tagged Amazon searches instead of guessed product URLs. Chewy approval is pending. Use only checked product ASINs with Amazon’s documented tagged text-link format; do not guess product IDs or advertise approval that has not been received.

`data/amazon_product_expansion.json` contains 3,975 screened expansion records added on September 21, 2026. `scripts/build_curated_product_expansion.py` assigns their stable IDs from 31 onward and deterministically synchronizes both catalog files without fetching Amazon or using the Creators API. The latest batch was captured from ordinary public Amazon search results, screened for relevance and duplicates, and stored without opening affiliate links. When adding another batch, verify the displayed title and ASIN first, append new stable records without reusing IDs, and update the builder's expected count rather than replacing an existing product.

`data/shopping_intent_seeds.json` is the reviewed core coverage set. `scripts/build_shopping_inventory.py` adds constraint variants and additional companion-animal concepts, then deterministically writes `data/shopping_intents.json`. Run the builder and review its count before committing inventory changes. Variants expand query coverage; they do not become claims of live stock, product testing, price, ratings, or suitability.

## Affiliate link maintenance

Run `python scripts/build_curated_product_expansion.py`, `python scripts/build_shopping_inventory.py`, and `python scripts/check_affiliate_links.py` before publishing. The checker validates exact Amazon links against their recorded ASIN and the required `pawpantry-20` tag, verifies that every active catalog record and shopping intent has an affiliate path, and checks Chewy links against approval and dated verification records without opening affiliate URLs or generating clicks. Verify the current listing and variant separately when adding an ASIN. Amazon’s Link Checker confirmed a sample of the documented format tags to this account. SiteStripe copying is not required. The API never publishes an Amazon product URL with an incorrect tag; it substitutes a clearly labeled tagged search action instead. Product search responses expose only validated links in `retailer_options`; each option includes `button_label`, `url`, `disclosure`, `affiliate`, `opens_after_user_click`, and `rel` so Muse can display it without a second API call. Muse must show the supplied disclosure beside the action and must never open a retailer or initiate a purchase without the user's click.

## Tests and deployment

Install `requirements-dev.txt`, then run `python -m pytest tests -q`. Tests use temporary SQLite databases, never production. They cover auth, validation, supply lifecycle and estimates, catalog updates preserving records, public HTML escaping, and retailer link validation. Production Postgres also needs a deployment smoke check.

Render uses `main`, `pip install -r requirements.txt`, and `uvicorn app:app --host 0.0.0.0 --port $PORT`. Keep auto-deploy set to On Commit and the Render GitHub app restricted to this repository. After a push, confirm Render shows the new commit deployed, `/health` succeeds, and the public catalog displays the expected products. The free Render service can sleep; persistent data does not eliminate cold starts.

For credential rotation, replace the relevant key in Render and its authorized client, then redeploy and verify the old key no longer works. Rotating the Muse key does not affect the owner workspace; rotating the owner key does not affect Muse. Never paste either key into chat or commit it to GitHub.

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
