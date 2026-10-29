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

## Things the maintainer will add himself (as part of the learning track)
Do NOT add these unless explicitly asked: tests, gunicorn, GitHub Actions
workflows, Azure configuration/infrastructure files. Do not run `git init`
or make commits — the maintainer handles git.

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

## File upload (implemented, local-disk only for now)
- `uploads.py` — validation + storage abstraction, used by `app.py`'s
  `/api/upload` (multipart POST: `key` + `file`) and
  `/staff/uploads/<token>` (signed download route).
- `Storage` is an interface selected by the `STORAGE_BACKEND` env var:
  - `LocalDiskStorage` (default) — writes to `uploads_private/`, outside
    `static/`, so nothing is directly web-reachable. Retrieval only through
    a short-lived signed token (`itsdangerous.URLSafeTimedSerializer`).
  - `R2Storage` — legacy Cloudflare R2 implementation, inactive. Per the
    roadmap it is to be replaced by an Azure Blob Storage backend
    implementing the same interface.

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

## Roadmap
1. **File upload → Azure Blob Storage**: an `AzureBlobStorage` backend
   behind the existing `Storage` interface. Private container, no public
   access; staff downloads via short-lived SAS URLs. Keep every protection
   from the threat model above.
2. **Stripe Checkout (test mode)**: price computed server-side from
   `products.json` (never trust a client-sent price), upload reference
   attached to the checkout session.
3. **Order hand-off → Power Automate**: after successful payment (Stripe
   webhook, signature-verified), POST the order details + SAS link to a
   Power Automate HTTP-triggered flow (e.g. notify staff, log the order).
4. **Monitoring → Application Insights**: request/error telemetry and
   custom events (price calculated, upload accepted/rejected, order paid).
5. **Deployment → Azure App Service via GitHub Actions**: CI/CD pipeline,
   secrets in App Service configuration / GitHub secrets — never in code.

## Known gaps
- No order/database model yet — each upload is a standalone file + JSON
  sidecar metadata, not tied to an order record.
- `app.secret_key` falls back to a dev-only default; a real `SECRET_KEY`
  env var is required for any deployment.
