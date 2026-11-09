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

from flask import Flask, abort, jsonify, render_template, request, send_file

from uploads import (
    ALLOWED_EXTENSIONS,
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

    logger.info(
        "upload stored: key=%s product=%s size=%d scan_engine=%s staff_link=%s",
        storage_key, key, len(data), scan.engine, storage.signed_url(storage_key),
    )

    return jsonify({"filename": file.filename, "size": len(data)})


@app.route("/staff/uploads/<token>")
def staff_download(token):
    if not isinstance(storage, LocalDiskStorage):
        abort(404)  # R2 uploads are retrieved via storage.signed_url() presigned URLs directly

    storage_key = storage.resolve_token(token)
    if not storage_key:
        abort(404)

    meta = storage.get_metadata(storage_key)
    return send_file(
        storage.path_for(storage_key),
        as_attachment=True,
        download_name=meta.get("original_filename", storage_key),
    )


@app.route("/api/price", methods=["POST"])
def calculate_price():
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    product = PRODUCTS.get(key)
    if not product:
        logger.warning("Price requested for unknown product: %s", key)
        return jsonify({"error": "Unknown product."}), 400

    ptype = product["pricing_type"]
    handler = _HANDLERS.get(ptype)
    if not handler:
        return jsonify({"error": "This product isn't available for online ordering yet."}), 400

    return handler(product, data)


def _not_confirmed():
    return jsonify({
        "error": "This combination isn't priced yet — please contact us for a quote."
    }), 400


def _price_tiered_multi_variant(product, data):
    variant_key = data.get("variant")
    variant = product["variants"].get(variant_key)
    if not variant:
        return jsonify({"error": "Please choose a stock."}), 400

    qty = str(data.get("qty"))
    sides = data.get("sides")

    if "sides_pricing" in variant:
        table = variant["sides_pricing"].get(sides)
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
        option = product["sides_options"].get(sides)
        if not option:
            return jsonify({"error": "Please choose a print side."}), 400
        table = option["pricing"]
    else:
        table = product.get("qty_pricing", {})

    price = table.get(qty)
    if price is None:
        return _not_confirmed()

    return jsonify({"price": price, "currency": "CAD", "note": "plus GST"})


def _price_fixed_qty_variant(product, data):
    variant_key = data.get("variant")
    variant = product["variants"].get(variant_key)
    if not variant:
        return jsonify({"error": "Please choose an option."}), 400

    return jsonify({
        "price": variant["price"],
        "currency": "CAD",
        "note": f"plus GST, for {product['fixed_qty']} units",
    })


def _price_per_unit_min_qty(product, data):
    size = data.get("size")
    unit_price = product["size_unit_price"].get(size)
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
    total = product["price"]
    addon_applied = False
    addon = product.get("addon_fee")
    if addon and data.get(addon["key"]):
        total += addon["amount"]
        addon_applied = True

    return jsonify({
        "price": total,
        "addon_applied": addon_applied,
        "currency": "CAD",
        "note": "plus GST",
    })


def _price_variant_fixed_price(product, data):
    variant_key = data.get("variant")
    variant = product["variants"].get(variant_key)
    if not variant:
        return jsonify({"error": "Please choose an option."}), 400

    return jsonify({"price": variant["price"], "currency": "CAD", "note": "plus GST"})


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
