import pytest

from app import MAX_UNITS_PER_ORDER, PRODUCTS

PURCHASABLE = [key for key, p in PRODUCTS.items() if p["pricing_type"] != "info_only"]


def price_for(client, payload):
    response = client.post("/api/price", json=payload)
    return response.status_code, response.get_json()


def first(mapping):
    """First key and value of a dict (catalogs keep a stable order)."""
    return next(iter(mapping.items()))


def valid_payload(key):
    """A valid /api/price request for any product, built from the catalog."""
    product = PRODUCTS[key]
    ptype = product["pricing_type"]
    if ptype == "tiered_multi_variant":
        variant_key, variant = first(product["variants"])
        if "sides_pricing" in variant:
            sides, table = first(variant["sides_pricing"])
            return {"key": key, "variant": variant_key, "sides": sides, "qty": first(table)[0]}
        return {"key": key, "variant": variant_key, "qty": first(variant["combined_pricing"])[0]}
    if ptype == "tiered_simple":
        if "sides_options" in product:
            sides, option = first(product["sides_options"])
            return {"key": key, "sides": sides, "qty": first(option["pricing"])[0]}
        return {"key": key, "qty": first(product["qty_pricing"])[0]}
    if ptype in ("fixed_qty_variant", "variant_fixed_price"):
        return {"key": key, "variant": first(product["variants"])[0], "qty": 1}
    if ptype == "per_unit_min_qty":
        return {"key": key, "size": first(product["size_unit_price"])[0], "qty": product["min_qty"]}
    if ptype == "fixed_price":
        return {"key": key, "qty": 1}
    raise AssertionError(f"Unknown pricing type: {ptype}")


# ---------- Every product ----------

@pytest.mark.parametrize("key", PURCHASABLE)
def test_every_product_can_be_priced(client, key):
    status, data = price_for(client, valid_payload(key))
    assert status == 200, data
    assert data["price"] > 0
    assert data["currency"] == "CAD"


# ---------- tiered_multi_variant: business cards ----------

def test_business_cards_price(client):
    expected = PRODUCTS["business_cards"]["variants"]["standard_c2s_silk"]["sides_pricing"]["4/0"]["100"]
    status, data = price_for(client, {"key": "business_cards", "variant": "standard_c2s_silk", "sides": "4/0", "qty": 100})
    assert status == 200
    assert data["price"] == expected


def test_rounded_corners_addon_is_added_once(client):
    cards = PRODUCTS["business_cards"]
    base = cards["variants"]["standard_c2s_silk"]["sides_pricing"]["4/0"]["500"]
    addon = cards["addons"]["rounded_corners"]["price"]
    status, data = price_for(client, {
        "key": "business_cards", "variant": "standard_c2s_silk", "sides": "4/0", "qty": 500,
        "rounded_corners": True,
    })
    assert status == 200
    assert data["price"] == base + addon


def test_stock_without_sides_uses_combined_pricing(client):
    expected = PRODUCTS["business_cards"]["variants"]["uv_coated_16pt"]["combined_pricing"]["1000"]
    status, data = price_for(client, {"key": "business_cards", "variant": "uv_coated_16pt", "qty": 1000})
    assert status == 200
    assert data["price"] == expected


def test_quantity_not_in_price_table_is_rejected(client):
    status, _ = price_for(client, {"key": "business_cards", "variant": "standard_c2s_silk", "sides": "4/0", "qty": 300})
    assert status == 400


def test_unknown_stock_is_rejected(client):
    status, _ = price_for(client, {"key": "business_cards", "variant": "gold_leaf", "sides": "4/0", "qty": 100})
    assert status == 400


def test_unknown_print_side_is_rejected(client):
    status, _ = price_for(client, {"key": "business_cards", "variant": "standard_c2s_silk", "sides": "9/9", "qty": 100})
    assert status == 400


# ---------- tiered_simple: envelopes, cheques ----------

def test_envelopes_price_depends_on_print_side(client):
    options = PRODUCTS["envelopes_no10"]["sides_options"]
    _, black = price_for(client, {"key": "envelopes_no10", "sides": "B/O", "qty": 100})
    _, colour = price_for(client, {"key": "envelopes_no10", "sides": "4/0", "qty": 100})
    assert black["price"] == options["B/O"]["pricing"]["100"]
    assert colour["price"] == options["4/0"]["pricing"]["100"]


def test_cheques_price_from_quantity_table(client):
    expected = PRODUCTS["manual_cheques"]["qty_pricing"]["250"]
    status, data = price_for(client, {"key": "manual_cheques", "qty": 250})
    assert status == 200
    assert data["price"] == expected


# ---------- per_unit_min_qty: posters ----------

def test_posters_price(client):
    posters = PRODUCTS["posters"]
    status, data = price_for(client, {"key": "posters", "size": "8.5x11", "qty": 20})
    assert status == 200
    assert data["price"] == posters["size_unit_price"]["8.5x11"] * 20


def test_posters_below_minimum(client):
    status, _ = price_for(client, {"key": "posters", "size": "8.5x11", "qty": PRODUCTS["posters"]["min_qty"] - 1})
    assert status == 400


def test_posters_unknown_size_is_rejected(client):
    status, _ = price_for(client, {"key": "posters", "size": "A0", "qty": 20})
    assert status == 400


def test_posters_non_numeric_quantity_is_rejected(client):
    status, _ = price_for(client, {"key": "posters", "size": "8.5x11", "qty": "twenty"})
    assert status == 400


# ---------- Quantity for per-item products ----------

def test_fixed_price_multiplies_by_quantity(client):
    status, data = price_for(client, {"key": "a_frame_sign", "qty": 3})
    assert status == 200
    assert data["price"] == PRODUCTS["a_frame_sign"]["price"] * 3


def test_fixed_price_defaults_to_one_unit(client):
    status, data = price_for(client, {"key": "a_frame_sign"})
    assert status == 200
    assert data["price"] == PRODUCTS["a_frame_sign"]["price"]


def test_setup_fee_is_charged_once_per_order(client):
    # One order = one design, so artwork setup is paid once no matter how many banners.
    banner = PRODUCTS["pull_up_banner"]
    status, data = price_for(client, {"key": "pull_up_banner", "qty": 3, banner["addon_fee"]["key"]: True})
    assert status == 200
    assert data["price"] == banner["price"] * 3 + banner["addon_fee"]["amount"]


def test_setup_fee_is_optional(client):
    banner = PRODUCTS["pull_up_banner"]
    status, data = price_for(client, {"key": "pull_up_banner", "qty": 3})
    assert status == 200
    assert data["price"] == banner["price"] * 3


def test_variant_fixed_price_multiplies_by_quantity(client):
    unit = PRODUCTS["yard_sign_4x8"]["variants"]["coroplast"]["price"]
    status, data = price_for(client, {"key": "yard_sign_4x8", "variant": "coroplast", "qty": 4})
    assert status == 200
    assert data["price"] == unit * 4


def test_presentation_folders_priced_per_set(client):
    unit = PRODUCTS["presentation_folders"]["variants"]["customer_assembly"]["price"]
    status, data = price_for(client, {"key": "presentation_folders", "variant": "customer_assembly", "qty": 2})
    assert status == 200
    assert data["price"] == unit * 2


def test_maximum_quantity_is_accepted(client):
    status, _ = price_for(client, {"key": "a_frame_sign", "qty": MAX_UNITS_PER_ORDER})
    assert status == 200


@pytest.mark.parametrize("bad_qty", [0, -1, MAX_UNITS_PER_ORDER + 1, "abc", "2.5"])
def test_quantity_out_of_range_is_rejected(client, bad_qty):
    status, _ = price_for(client, {"key": "a_frame_sign", "qty": bad_qty})
    assert status == 400


# ---------- Bad requests must be 400, never 500 ----------

def test_info_only_product_cannot_be_priced(client):
    status, _ = price_for(client, {"key": "design_services"})
    assert status == 400


def test_unknown_product_is_rejected(client):
    status, _ = price_for(client, {"key": "does_not_exist"})
    assert status == 400


@pytest.mark.parametrize("body", [[1, 2], "just text", 42, None])
def test_request_body_that_is_not_an_object_is_rejected(client, body):
    response = client.post("/api/price", json=body)
    assert response.status_code == 400


def test_invalid_json_is_rejected(client):
    response = client.post("/api/price", data="{not json", content_type="application/json")
    assert response.status_code == 400


@pytest.mark.parametrize("field", ["key", "variant", "sides", "size"])
@pytest.mark.parametrize("value", [["a"], {"a": 1}, 123, True])
def test_wrong_value_types_are_rejected_not_crashing(client, field, value):
    # Lists and objects used to crash the dict lookup with a TypeError (HTTP 500).
    for key in PURCHASABLE:
        payload = {**valid_payload(key), field: value}
        status, _ = price_for(client, payload)
        assert status < 500, f"{key}: {field}={value!r} caused a server error"
