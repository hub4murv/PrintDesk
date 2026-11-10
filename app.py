"""
PrintDesk — self-serve online ordering for print shops (portfolio project).

Run locally:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

Debug mode is OFF unless FLASK_DEBUG=1 is set in the environment.
"""

import os

# Application Insights: only active when the connection string is set
# (in Azure). Must run before Flask is imported so requests are tracked.
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor()

import io
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests


from flask import Flask, abort, jsonify, render_template, request, send_file

from uploads import (
    ALLOWED_EXTENSIONS,
    is_valid_storage_key,
    MAX_UPLOAD_BYTES,
    LocalDiskStorage,
    UploadRejected,
    build_storage,
    scan_file,
    validate_upload,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_PATH = os.path.join(BASE_DIR, "products.json")

with open(PRODUCTS_PATH, encoding="utf-8") as f:
    PRODUCTS = json.load(f)

storage = build_storage(app)

FLOW_ORDER_URL = os.environ.get("FLOW_ORDER_URL")
# Design links in orders stay valid for 6 days: longer than the 3-day approval
# timeout, shorter than the 7-day maximum of a user delegation SAS.
DESIGN_LINK_SECONDS = 6 * 24 * 3600
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOGO_FILE = "images/logo.svg"
PRODUCT_IMAGE_DIR = "images/products"


def _static_file_exists(relative_path):
    return os.path.isfile(os.path.join(STATIC_DIR, relative_path))


@app.context_processor
def inject_branding_helpers():
    """Optional assets, checked per request so files can be dropped in
    without restarting the server:
      - static/images/logo.svg -> replaces the "PrintDesk" text wordmark
      - static/images/products/<product_key>.webp -> replaces the SVG icon
    """
    def product_image(key):
        rel = f"{PRODUCT_IMAGE_DIR}/{key}.webp"
        return rel if _static_file_exists(rel) else None

    return {
        "logo_file": LOGO_FILE if _static_file_exists(LOGO_FILE) else None,
        "product_image": product_image,
    }


@app.route("/")
def index():
    cards = []
    for key, p in PRODUCTS.items():
        cards.append({
            "key": key,
            "category": p["category"],
            "tagline": p["tagline"],
            "from_price": p["from_price"],
            "icon": p["icon"],
            "purchasable": p["pricing_type"] != "info_only",
        })
    return render_template("index.html", cards=cards)


@app.route("/product/<key>")
def product_detail(key):
    product = PRODUCTS.get(key)
    if not product:
        abort(404)
    accept = ",".join(f".{ext}" for ext in sorted(ALLOWED_EXTENSIONS))
    return render_template("product.html", key=key, product=product, upload_accept=accept)


@app.errorhandler(413)
def file_too_large(_e):
    max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    return jsonify({"error": f"File is too large. Maximum size is {max_mb} MB."}), 413


@app.route("/api/upload", methods=["POST"])
def upload_design():
    key = request.form.get("key")
    if key not in PRODUCTS:
        return jsonify({"error": "Unknown product."}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Please choose a file."}), 400

    data = file.stream.read()
    if not data:
        return jsonify({"error": "The uploaded file is empty."}), 400

    try:
        ext = validate_upload(file.filename, data)
    except UploadRejected as e:
        return jsonify({"error": e.message}), 400

    scan = scan_file(data)
    storage_key = f"{uuid.uuid4().hex}.{ext}"
    storage.save(io.BytesIO(data), storage_key)
    storage.save_json(storage_key, {
        "original_filename": file.filename,
        "product": key,
        "size": len(data),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "scan_engine": scan.engine,
        "scan_clean": scan.clean,
    })

    # Never log download links: a signed link is a password for that file.
    logger.info(
        "upload stored: key=%s product=%s size=%d scan_engine=%s",
        storage_key, key, len(data), scan.engine,
    )

    # file_id goes back with the order, so the flow gets a link to this design.
    return jsonify({"filename": file.filename, "size": len(data), "file_id": storage_key})


@app.route("/staff/uploads/<token>")
def staff_download(token):
    if not isinstance(storage, LocalDiskStorage):
        abort(404)  # Azure uploads are downloaded with SAS links straight from Blob Storage

    storage_key = storage.resolve_token(token)
    if not storage_key:
        abort(404)

    meta = storage.get_metadata(storage_key)
    return send_file(
        storage.path_for(storage_key),
        as_attachment=True,
        download_name=meta.get("original_filename", storage_key),
    )


def _json_body():
    """Request JSON as a dict. Anything else (a list, a string, invalid JSON) becomes {}."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _pick(options, choice):
    """Look up an option sent by the browser.

    Lists, objects and other non-string values are treated as "not found" instead of
    crashing the dict lookup (a TypeError would turn a bad request into a 500).
    """
    return options.get(choice) if isinstance(choice, str) else None


@app.route("/api/price", methods=["POST"])
def calculate_price():
    data = _json_body()
    key = data.get("key")
    product = _pick(PRODUCTS, key)
    if not product:
        logger.warning("Price requested for unknown product: %s", key)
        return jsonify({"error": "Unknown product."}), 400

    ptype = product["pricing_type"]
    handler = _HANDLERS.get(ptype)
    if not handler:
        return jsonify({"error": "This product isn't available for online ordering yet."}), 400

    return handler(product, data)

@app.route("/api/order", methods=["POST"])
def place_order():
    data = _json_body()

    customer_name = str(data.get("customer_name") or "").strip()
    if not customer_name or len(customer_name) > 100:
        return jsonify({"error": "Please enter your name or company (up to 100 characters)."}), 400

    product = _pick(PRODUCTS, data.get("key"))
    handler = _HANDLERS.get(product["pricing_type"]) if product else None
    if not handler:
        return jsonify({"error": "This product can't be ordered online."}), 400

    # Recalculate the price on the server. Never trust a price sent by the browser.
    result = handler(product, data)
    if isinstance(result, tuple):  # (response, 400) means the options are invalid
        return result
    priced = result.get_json()
    amount = priced["price"]

    file_id = data.get("file_id")
    if not is_valid_storage_key(file_id) or not storage.exists(file_id):
        return jsonify({"error": "Please upload your design first."}), 400

    if not FLOW_ORDER_URL:
        logger.warning("FLOW_ORDER_URL is not set; online ordering is disabled")
        return jsonify({"error": "Online ordering is not available right now."}), 503

    order_id = uuid.uuid4().hex[:8].upper()
    payload = {
        "order_id": order_id,
        "customer_name": customer_name,
        "product": product["category"],
        "details": _describe_options(product, data, priced),
        "amount": amount,
        # Absolute link the manager can open from Teams, the approval or Dataverse.
        "design_url": urljoin(request.host_url, storage.signed_url(file_id, expires_in=DESIGN_LINK_SECONDS)),
    }
    try:
        response = requests.post(FLOW_ORDER_URL, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Could not send order %s to Power Automate", order_id)
        return jsonify({"error": "We couldn't place your order. Please try again."}), 502

    return jsonify({"order_id": order_id, "amount": amount, "status": "received"}), 202


def _describe_options(product, data, priced):
    """Human-readable summary of what to print, e.g. "Coroplast · qty 4".

    Only called after the pricing handler accepted the options, so every value is valid.
    """
    parts = []
    variant = _pick(product.get("variants", {}), data.get("variant"))
    if variant:
        parts.append(variant["label"])
    sides_option = _pick(product.get("sides_options", {}), data.get("sides"))
    if sides_option:
        parts.append(sides_option["label"])
    elif isinstance(data.get("sides"), str):
        parts.append(data["sides"])  # e.g. "4/0" for business cards
    if _pick(product.get("size_unit_price", {}), data.get("size")) is not None:
        parts.append(f'{data["size"]}"')

    qty = priced.get("qty") or data.get("qty") or 1
    if product["pricing_type"] == "fixed_qty_variant":
        parts.append(f'{qty} × {product["fixed_qty"]} units')
    else:
        parts.append(f"qty {qty}")

    for addon_key, addon in product.get("addons", {}).items():
        if data.get(addon_key):
            parts.append(addon["label"])
    fee = product.get("addon_fee")
    if fee and data.get(fee["key"]):
        parts.append(fee["label"])
    return " · ".join(parts)


def _not_confirmed():
    return jsonify({
        "error": "This combination isn't priced yet — please contact us for a quote."
    }), 400


def _price_tiered_multi_variant(product, data):
    variant_key = data.get("variant")
    variant = _pick(product["variants"], variant_key)
    if not variant:
        return jsonify({"error": "Please choose a stock."}), 400

    qty = str(data.get("qty"))
    sides = data.get("sides")

    if "sides_pricing" in variant:
        table = _pick(variant["sides_pricing"], sides)
        if table is None:
            return jsonify({"error": "Please choose a print side."}), 400
    else:
        table = variant.get("combined_pricing")

    price = table.get(qty) if table else None
    if price is None:
        return _not_confirmed()

    total = price
    addon_total = 0
    for addon_key, addon in product.get("addons", {}).items():
        if data.get(addon_key):
            addon_total += addon["price"]
            total += addon["price"]

    return jsonify({
        "price": total,
        "base_price": price,
        "addon_total": addon_total,
        "currency": "CAD",
        "note": "plus GST",
    })


def _price_tiered_simple(product, data):
    qty = str(data.get("qty"))

    if "sides_options" in product:
        sides = data.get("sides")
        option = _pick(product["sides_options"], sides)
        if not option:
            return jsonify({"error": "Please choose a print side."}), 400
        table = option["pricing"]
    else:
        table = product.get("qty_pricing", {})

    price = table.get(qty)
    if price is None:
        return _not_confirmed()

    return jsonify({"price": price, "currency": "CAD", "note": "plus GST"})


MAX_UNITS_PER_ORDER = 100


def _parse_units(data):
    """Quantity for products priced per piece (or per set). Defaults to 1.

    Returns None when the value isn't a whole number from 1 to MAX_UNITS_PER_ORDER.
    """
    raw = data.get("qty")
    if raw is None or raw == "":
        return 1
    try:
        units = int(raw)
    except (TypeError, ValueError):
        return None
    return units if 1 <= units <= MAX_UNITS_PER_ORDER else None


def _invalid_units():
    return jsonify({
        "error": f"Please enter a quantity from 1 to {MAX_UNITS_PER_ORDER}."
    }), 400


def _price_fixed_qty_variant(product, data):
    variant_key = data.get("variant")
    variant = _pick(product["variants"], variant_key)
    if not variant:
        return jsonify({"error": "Please choose an option."}), 400

    sets = _parse_units(data)
    if sets is None:
        return _invalid_units()

    fixed_qty = product["fixed_qty"]
    return jsonify({
        "price": round(variant["price"] * sets, 2),
        "unit_price": variant["price"],
        "qty": sets,
        "currency": "CAD",
        "note": f"plus GST, {sets * fixed_qty} units ({sets} × {fixed_qty})",
    })


def _price_per_unit_min_qty(product, data):
    size = data.get("size")
    unit_price = _pick(product["size_unit_price"], size)
    if unit_price is None:
        return jsonify({"error": "Please choose a size."}), 400

    try:
        qty = int(data.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0

    min_qty = product["min_qty"]
    if qty < min_qty:
        return jsonify({"error": f"Minimum order is {min_qty} units."}), 400

    total = round(unit_price * qty, 2)
    return jsonify({
        "price": total,
        "unit_price": unit_price,
        "currency": "CAD",
        "note": "plus GST",
    })


def _price_fixed_price(product, data):
    units = _parse_units(data)
    if units is None:
        return _invalid_units()

    total = product["price"] * units
    addon_applied = False
    addon = product.get("addon_fee")
    if addon and data.get(addon["key"]):
        total += addon["amount"]  # one-time fee per order, not per unit
        addon_applied = True

    return jsonify({
        "price": round(total, 2),
        "unit_price": product["price"],
        "qty": units,
        "addon_applied": addon_applied,
        "currency": "CAD",
        "note": "plus GST" + (", setup fee charged once per order" if addon_applied else ""),
    })


def _price_variant_fixed_price(product, data):
    variant_key = data.get("variant")
    variant = _pick(product["variants"], variant_key)
    if not variant:
        return jsonify({"error": "Please choose an option."}), 400

    units = _parse_units(data)
    if units is None:
        return _invalid_units()

    return jsonify({
        "price": round(variant["price"] * units, 2),
        "unit_price": variant["price"],
        "qty": units,
        "currency": "CAD",
        "note": "plus GST",
    })


_HANDLERS = {
    "tiered_multi_variant": _price_tiered_multi_variant,
    "tiered_simple": _price_tiered_simple,
    "fixed_qty_variant": _price_fixed_qty_variant,
    "per_unit_min_qty": _price_per_unit_min_qty,
    "fixed_price": _price_fixed_price,
    "variant_fixed_price": _price_variant_fixed_price,
}


if __name__ == "__main__":
    # Debug mode is opt-in: set FLASK_DEBUG=1 for local development only.
    # Never enable it on anything reachable from outside localhost -- the
    # interactive Werkzeug debugger lets anyone who triggers an unhandled
    # exception run arbitrary Python on the server.
    debug = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    app.run(debug=debug, port=5000)
