# Paw Pantry

Private single-owner pet profiles, supply estimates, and a public pet-supply catalog. The website is hosted on Render at https://paw-pantry.onrender.com. Production data is stored separately in Neon Postgres so application redeploys do not erase it.

## Run locally

Use Python 3.14 (Render runtime; tests also run on 3.12). Install `requirements.txt`, set `PAW_PANTRY_API_KEY` and `MUSE_CONNECTOR_API_KEY` to different private random values, and run `uvicorn app:app`. Without `DATABASE_URL`, local development uses SQLite. Production must set `DATABASE_URL` to the Neon TLS connection URL. Never commit or share these secrets.

Public pages: `/`, `/catalog`, `/guide`, `/guides/{slug}`, `/calculator`, `/about`, `/privacy`, `/terms`, `/documentation`, `/docs`, `/openapi.json`. `/health` checks database connectivity, `/ready` confirms the connector credential and database are configured, and `/ping` returns an uncached 204 for process liveness without touching the database. API requests use the `X-API-Key` header. Missing or incorrect credentials return 401.

## Current connector scope

The published Muse contract is stateless and excludes every private pet-profile and supply-record route. Muse receives `MUSE_CONNECTOR_API_KEY`, which can call only catalog search, shopping options, retailer links, and the stateless refill estimator. The separate `PAW_PANTRY_API_KEY` remains required for all stored pet and supply operations. Startup fails if the two keys are equal.

Supported examples:

- Search the starter catalog by species, category, or keyword. Each result includes
  validated `retailer_options` that Muse can render as direct external buttons with
  the supplied affiliate disclosure.
- Handle open-ended requests through `/shopping-options`. It returns ranked curated
  matches plus a tagged Amazon search-results action for broader choice. The search
  action is clearly identified as changing retailer results rather than a verified
  individual product recommendation.
- Calculate how many days remain and when to consider reordering without storing the inputs.
- Get an available retailer link with its disclosure, without placing an order.

There are no scheduled reminders, automatic orders, checkout, delivery tracking, live prices, veterinary recommendations, or automatic ingredient/allergy filtering. An allergy recorded in a profile does not certify any product as suitable. Reorder dates are estimates, including past dates for overdue supplies. Check actual supplies and the current label.

The checked local catalog is deliberately smaller than Amazon's catalog. Paw Pantry
uses a broad Amazon pet-supply search action for long-tail shopping requests. The
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

`/catalog-stats` reports the current inventory without authentication. The current
seed has 30 stable records: 25 active curated products, five retained retired variants,
23 active verified Amazon product links, and no active Chewy links. The active set
covers seven species and 14 supply categories. `/shopping-options` adds a broader
tagged Amazon search for requests that exceed that curated set.

When `OPENAI_API_KEY` is set, product search uses `text-embedding-3-small` with
256-dimensional embeddings to combine semantic relevance with the offline keyword
score. Catalog vectors are cached in memory; each request embeds only the query after
the first catalog pass. If the API is unavailable or the key is absent, matching falls
back automatically to the tested offline ranker. Optional settings are
`OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMENSIONS`, and
`OPENAI_EMBEDDING_TIMEOUT_SECONDS`. Never commit the API key.

## Catalog maintenance

`data/seed_products.json` contains stable product IDs. Startup updates those managed IDs and inserts new ones in a transaction; it preserves pets, supplies, and other product rows. Never recycle an ID for a different product. Schema changes need a separate migration plan; startup table creation does not migrate existing columns.

`data/catalog_sources.json` records checked manufacturer pages, Amazon ASINs, checked variants, and link provenance. Five older package/variant records are retired from browsing and search but retained in the database for existing supplies; their alternatives have new IDs. 23 Amazon purchase links are currently configured. Two current entries still lack verified Amazon matches, and Chewy approval is pending. Missing links return 409 rather than a placeholder. Use only verified product ASINs with Amazon’s documented simple text link format; do not guess product IDs or advertise approval that has not been received.

## Affiliate link maintenance

Run `python scripts/check_affiliate_links.py` before publishing. It checks Amazon links against their recorded ASIN and the required `pawpantry-20` tag, and Chewy links against approval and dated verification records without opening affiliate URLs or generating clicks. Verify the current product page and variant separately when adding an ASIN. Amazon’s Link Checker confirmed a sample of the documented format tags to this account. SiteStripe copying is not required. The API also rejects Amazon links with an incorrect tag. Product search responses expose only validated links in `retailer_options`; each option includes `button_label`, `url`, `disclosure`, `affiliate`, `opens_after_user_click`, and `rel` so Muse can display it without a second API call. Muse must show the supplied disclosure beside the action and must never open a retailer or initiate a purchase without the user's click.

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
