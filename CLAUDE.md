# PrintDesk — project context for Claude

## What this is
PrintDesk is a generic, self-serve online ordering app for print shops:
customers pick a product, choose options, see a live price, and upload
their print-ready design — no phone call or email back-and-forth.

It is a **portfolio project** (public GitHub repo) and the capstone of an
Azure learning track: Azure App Service, GitHub Actions CI/CD, Application
Insights and Power Automate integration. It is not tied to any real shop.

**All prices in `products.json` are demo values.** Never replace them with a
real company's price list, and keep the "Demo pricing for portfolio purposes."
notice visible on the site.

## Stack
- Flask (Python), Jinja2 templates, vanilla JS (no frontend framework).
- No database yet — products and pricing live in `products.json`.
- No auth, no payments yet (see Roadmap).
- Dependencies are deliberately minimal (`requirements.txt`). Don't add
  frameworks or libraries unless explicitly asked.

## Working rules
- Tests, CI/CD workflow and Azure resources already exist. Keep the test suite
  green and add tests with every change.
- Don't change `.github/workflows/` or Azure infrastructure unless asked.
- Don't commit or push; the maintainer handles git.

## Architecture
- `products.json` — the catalog (17 items). Each product has a
  `pricing_type` discriminator — one of `tiered_multi_variant`,
  `tiered_simple`, `fixed_qty_variant`, `per_unit_min_qty`, `fixed_price`,
  `variant_fixed_price`, `info_only` — that decides both which UI controls
  render and how `/api/price` computes the total. Deliberate choice to avoid
  one-off hardcoded pricing functions per product.
- `app.py` — one handler function per `pricing_type` (see the `_HANDLERS`
  dict). Adding a product almost never needs Python changes — just a new
  entry in `products.json` matching an existing `pricing_type` shape.
- Pricing rules to keep when editing demo prices: totals rise with quantity;
  4/4 (double-sided) costs more than 4/0; `from_price` = the cheapest option.
- `templates/product.html` — one template with a Jinja
  `{% if product.pricing_type == ... %}` block per shape, each with scoped
  inline JS that posts the right payload to `/api/price`.
- `templates/_icons.html` — hand-drawn inline SVG icons per category, used
  as the fallback product visual.
- **Optional images** (checked per request by a context processor in
  `app.py`, so files can be dropped in without a restart):
  - `static/images/logo.svg` → replaces the "PrintDesk" text wordmark in
    the header.
  - `static/images/products/<product_key>.webp` → shown on the product card
    and product page (alt text = category name, `loading="lazy"`); falls
    back to the SVG icon if the file doesn't exist.
- Visual identity: "print shop" direction — registration-mark corner
  accents on hover, cyan accent, Space Grotesk (display) + Inter (body).
- Debug mode is read from `FLASK_DEBUG` and is **off by default**.

## File upload
- `uploads.py` — validation + storage abstraction, used by `app.py`'s
  `/api/upload` (multipart POST: `key` + `file`) and
  `/staff/uploads/<token>` (signed download route).
- `Storage` is an interface selected by the `STORAGE_BACKEND` env var:
  - `LocalDiskStorage` (default) — writes to `uploads_private/`, outside
    `static/`, so nothing is directly web-reachable. Retrieval only through
    a short-lived signed token (`itsdangerous.URLSafeTimedSerializer`).
  - `AzureBlobStorage` (production, `STORAGE_BACKEND=azure`) — private
    container, accessed with the App Service managed identity via
    `DefaultAzureCredential`. Shared-key access is disabled on the account.
    Staff links are read-only user delegation SAS URLs (max 7 days; orders use 6).
- The browser gets `file_id` (the storage key) after upload and sends it with
  the order; `/api/order` checks its exact format and that the file exists.
- Never log signed links: a SAS URL is a credential.

### Upload threat model (keep these protections in any new backend)
- **File-type spoofing** — extension whitelist (pdf/jpg/jpeg/png/ai/eps/
  tif/tiff) **plus** a hand-rolled magic-byte check per extension. Never
  trust the extension or the client-reported MIME type. (Not
  `python-magic`: it segfaulted on import on the Windows dev machine.)
- **Archives / zip bombs** — anything with a ZIP signature is rejected
  regardless of extension. Any future processing of uploads (thumbnails,
  previews) must run isolated from the web app, with size limits.
- **Oversized uploads / DoS** — server-side `MAX_CONTENT_LENGTH` (50 MB).
  Rate limiting on the upload endpoint is still to do.
- **Path traversal** — storage keys are `uuid4().hex + ext`; the client's
  filename is never used in a path, only kept as metadata.
- **Leaking one customer's file to another** — unguessable keys, private
  storage, short-lived signed URLs only; never a public bucket/container.
- **Malware in PDFs / Office files** — antivirus scanning is **NOT wired
  up**: `scan_file()` is a deliberate no-op stub that logs a warning. Do not
  remove the warning or claim files are scanned anywhere in the UI until a
  real scanner exists.
- **SVG / XSS** — SVG is intentionally not accepted from customers.
- **Personal data in designs** (names, phones, addresses on business cards)
  — a retention policy (how long files are kept, who can access them) is
  still to be defined before handling real customer files.
- **Public demo exposure** — once deployed publicly, anyone can upload to
  the demo. Before going live, consider disabling uploads in the public
  demo or adding rate limiting + automatic cleanup.

## Done
Azure App Service hosting, GitHub Actions CI/CD with OIDC, Application
Insights (OpenTelemetry), Azure Blob Storage with managed identity, order
hand-off to a Power Automate HTTP-triggered flow (Dataverse, approvals,
Teams), quantity for per-item products.

## Next
1. Antivirus scanning via a blob-triggered Azure Function.
2. Rate limiting on `/api/upload` and `/api/order` (public demo).
3. Staff page (Entra ID sign-in) that issues fresh design links.
4. Single source for the $500 approval threshold.
5. Stripe Checkout (test mode): price computed server-side, never from the client.

## Known gaps
- `app.secret_key` falls back to a dev-only default; a real `SECRET_KEY`
  env var is required for any deployment.
- Retention: designs are deleted after 90 days by a storage lifecycle policy.
