# PrintDesk

**Self-serve online ordering for print shops.** Customers pick a product,
choose stock, sides and quantity, see the price update live, and upload a
print-ready design — no phone call, no emailing files back and forth.

> **Portfolio project.** PrintDesk is not a real print shop. All prices are
> demo values and no orders are fulfilled.

## Features

- **Product catalog** with 17 example products — business cards, flyers,
  envelopes, posters, banners, signs and more.
- **Data-driven pricing engine** — every product declares a `pricing_type`
  in `products.json` (tiered by quantity, per-unit with a minimum, fixed
  price, option-based, …). One handler per type; adding a product usually
  needs no code changes.
- **Live price calculation** via a small JSON API (`POST /api/price`).
- **Option pickers** for stock, print sides, size and quantity, plus add-ons
  such as rounded corners.
- **Secure design upload**: extension whitelist + magic-byte content check,
  50 MB limit, random storage keys, private storage with signed,
  time-limited download links for staff.
- **Optional product photos and logo** — drop a `.webp` / `.svg` into
  `static/` and it replaces the built-in icons automatically.
- Responsive layout, no frontend framework.

## Tech stack

- **Backend:** Python, Flask, Jinja2
- **Frontend:** HTML, CSS, vanilla JavaScript
- **Data:** JSON product catalog (no database yet)
- **Planned:** Azure App Service, GitHub Actions, Azure Blob Storage,
  Application Insights, Power Automate, Stripe (test mode)

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLASK_DEBUG` | off | Set to `1` to enable debug mode — local development only. |
| `SECRET_KEY` | dev-only placeholder | Signs staff download links. Set a real value for any deployment. |
| `STORAGE_BACKEND` | `local` | `local` (server disk, for development) or `azure` (Blob Storage). |
| `UPLOAD_DIR` | `uploads_private/` | Folder for uploads with the local backend. |
| `AZURE_STORAGE_ACCOUNT_URL` | — | Blob endpoint, e.g. `https://<account>.blob.core.windows.net`. Access uses managed identity, no keys. |
| `AZURE_STORAGE_CONTAINER` | `designs` | Private container for customer designs. |
| `FLOW_ORDER_URL` | — | Power Automate HTTP trigger URL. Secret: treat it like a password. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | — | Enables Application Insights monitoring. |

### Optional images

- `static/images/logo.svg` — replaces the "PrintDesk" text logo.
- `static/images/products/<product_key>.webp` — product photo
  (e.g. `business_cards.webp`). Products without one show an icon.

## Project structure

```
app.py              Flask routes + pricing handlers
uploads.py          Upload validation and storage backends
products.json       Product catalog and demo pricing
templates/          Jinja2 templates (catalog, product page, icons)
static/             CSS and optional images
```

## Roadmap

- [ ] Store uploads in **Azure Blob Storage** (private container, short-lived SAS URLs)
- [ ] **Stripe Checkout** in test mode
- [ ] After payment, send the order to a **Power Automate** HTTP-triggered flow
- [ ] **Application Insights** monitoring
- [ ] Deploy to **Azure App Service** with **GitHub Actions** CI/CD
- [ ] Antivirus scanning for uploaded files
