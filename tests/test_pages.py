import pytest
from markupsafe import escape

from app import PRODUCTS

PURCHASABLE = [key for key, p in PRODUCTS.items() if p["pricing_type"] != "info_only"]
INFO_ONLY = [key for key, p in PRODUCTS.items() if p["pricing_type"] == "info_only"]


def html_text(text):
    """How Jinja writes a string into the page (e.g. ' becomes &#39;)."""
    return str(escape(text)).encode()


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"PrintDesk" in response.data


def test_home_page_lists_every_product(client):
    response = client.get("/")
    for product in PRODUCTS.values():
        assert html_text(product["category"]) in response.data


@pytest.mark.parametrize("key", PRODUCTS)
def test_every_product_page_renders(client, key):
    # Catches template errors in the Jinja branch of each pricing type.
    response = client.get(f"/product/{key}")
    assert response.status_code == 200
    assert html_text(PRODUCTS[key]["category"]) in response.data


@pytest.mark.parametrize("key", PURCHASABLE)
def test_purchasable_products_show_the_order_form(client, key):
    response = client.get(f"/product/{key}")
    assert b'id="order-form"' in response.data
    assert b'id="dropzone"' in response.data


@pytest.mark.parametrize("key", INFO_ONLY)
def test_info_only_products_have_no_order_form(client, key):
    response = client.get(f"/product/{key}")
    assert b'id="order-form"' not in response.data


def test_unknown_product_page_returns_404(client):
    assert client.get("/product/does_not_exist").status_code == 404
