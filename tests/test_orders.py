import re

import pytest
import requests

import app as app_module
from app import PRODUCTS

@pytest.fixture
def order(uploaded_file):
    """A valid order request for 100 business cards with an uploaded design."""
    return {
        "key": "business_cards",
        "variant": "standard_c2s_silk",
        "sides": "4/0",
        "qty": 100,
        "customer_name": "Test Co",
        "file_id": uploaded_file,
    }


class FakeResponse:
    def __init__(self, status_code=202):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


@pytest.fixture
def flow(monkeypatch):
    """A fake Power Automate flow. Records every call instead of sending it."""
    calls = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(app_module, "FLOW_ORDER_URL", "https://example.test/flow")
    monkeypatch.setattr(app_module.requests, "post", fake_post)
    return calls


def place(client, payload):
    response = client.post("/api/order", json=payload)
    return response.status_code, response.get_json()


# ---------- Happy path ----------

def test_order_is_sent_to_flow(client, order, flow):
    status, data = place(client, order)
    assert status == 202
    assert len(flow) == 1
    sent = flow[0]
    assert sent["url"] == "https://example.test/flow"
    assert sent["json"]["order_id"] == data["order_id"]
    assert sent["json"]["customer_name"] == "Test Co"
    assert sent["json"]["product"] == PRODUCTS["business_cards"]["category"]


def test_order_id_is_8_uppercase_hex_characters(client, order, flow):
    _, data = place(client, order)
    assert re.fullmatch(r"[0-9A-F]{8}", data["order_id"])


def test_every_order_gets_a_new_id(client, order, flow):
    _, first = place(client, order)
    _, second = place(client, order)
    assert first["order_id"] != second["order_id"]


def test_order_sends_server_price_to_flow(client, order, flow):
    # The browser claims the price is $1. The server must ignore it.
    status, _ = place(client, {**order, "price": 1, "amount": 1})
    expected = PRODUCTS["business_cards"]["variants"]["standard_c2s_silk"]["sides_pricing"]["4/0"]["100"]
    assert status == 202
    assert flow[0]["json"]["amount"] == expected


def test_order_amount_includes_quantity(client, flow, uploaded_file):
    status, data = place(client, {"key": "a_frame_sign", "qty": 3, "customer_name": "Test Co", "file_id": uploaded_file})
    assert status == 202
    assert data["amount"] == PRODUCTS["a_frame_sign"]["price"] * 3
    assert flow[0]["json"]["amount"] == data["amount"]


def test_flow_call_has_a_timeout(client, order, flow):
    place(client, order)
    assert flow[0]["timeout"] and flow[0]["timeout"] <= 30


def test_customer_name_is_trimmed(client, order, flow):
    place(client, {**order, "customer_name": "   Test Co   "})
    assert flow[0]["json"]["customer_name"] == "Test Co"


def test_customer_name_of_100_characters_is_accepted(client, order, flow):
    status, _ = place(client, {**order, "customer_name": "A" * 100})
    assert status == 202


# ---------- Validation: nothing reaches the flow ----------

@pytest.mark.parametrize("name", ["", "   ", None, "A" * 101])
def test_invalid_customer_name_is_rejected(client, order, flow, name):
    status, _ = place(client, {**order, "customer_name": name})
    assert status == 400
    assert flow == []


def test_invalid_options_are_rejected_before_calling_flow(client, order, flow):
    status, _ = place(client, {**order, "qty": 300})  # not in the price table
    assert status == 400
    assert flow == []


def test_info_only_product_cannot_be_ordered(client, flow, uploaded_file):
    status, _ = place(client, {"key": "design_services", "customer_name": "Test Co", "file_id": uploaded_file})
    assert status == 400
    assert flow == []


def test_unknown_product_cannot_be_ordered(client, order, flow):
    status, _ = place(client, {**order, "key": "does_not_exist"})
    assert status == 400
    assert flow == []


@pytest.mark.parametrize("body", [[1, 2], "just text", None])
def test_order_body_that_is_not_an_object_is_rejected(client, flow, body):
    response = client.post("/api/order", json=body)
    assert response.status_code == 400
    assert flow == []


# ---------- When the flow is unavailable ----------

def test_order_disabled_without_flow_url(client, order, monkeypatch):
    monkeypatch.setattr(app_module, "FLOW_ORDER_URL", None)
    status, data = place(client, order)
    assert status == 503
    assert data["error"]


def test_flow_network_error_returns_502(client, order, monkeypatch):
    def broken_post(url, json, timeout):
        raise requests.ConnectionError("flow unreachable")

    monkeypatch.setattr(app_module, "FLOW_ORDER_URL", "https://example.test/flow")
    monkeypatch.setattr(app_module.requests, "post", broken_post)
    status, data = place(client, order)
    assert status == 502
    assert data["error"]


def test_flow_timeout_returns_502(client, order, monkeypatch):
    def slow_post(url, json, timeout):
        raise requests.Timeout("flow too slow")

    monkeypatch.setattr(app_module, "FLOW_ORDER_URL", "https://example.test/flow")
    monkeypatch.setattr(app_module.requests, "post", slow_post)
    status, _ = place(client, order)
    assert status == 502


def test_flow_http_error_returns_502(client, order, monkeypatch):
    # e.g. the flow is turned off and Power Automate answers with an error code
    monkeypatch.setattr(app_module, "FLOW_ORDER_URL", "https://example.test/flow")
    monkeypatch.setattr(app_module.requests, "post", lambda url, json, timeout: FakeResponse(404))
    status, _ = place(client, order)
    assert status == 502


# ---------- Design file and order details ----------

def test_flow_gets_a_working_link_to_the_design(client, order, flow):
    status, _ = place(client, order)
    assert status == 202
    link = flow[0]["json"]["design_url"]
    assert link.startswith("http://localhost/staff/uploads/")
    # The link really downloads the uploaded design
    download = client.get(link.replace("http://localhost", ""))
    assert download.status_code == 200
    assert download.data.startswith(b"\x89PNG")


def test_flow_gets_readable_order_details(client, order, flow):
    place(client, {**order, "rounded_corners": True})
    details = flow[0]["json"]["details"]
    label = PRODUCTS["business_cards"]["variants"]["standard_c2s_silk"]["label"]
    assert label in details
    assert "4/0" in details
    assert "qty 100" in details
    assert "Rounded corners" in details


@pytest.mark.parametrize("payload, expected", [
    ({"key": "yard_sign_4x8", "variant": "polymetal", "qty": 4}, ["Polymetal", "qty 4"]),
    ({"key": "posters", "size": "12x18", "qty": 40}, ['12x18"', "qty 40"]),
    ({"key": "presentation_folders", "variant": "customer_assembly", "qty": 2}, ["Customer assembly", "2 × 100 units"]),
    ({"key": "envelopes_no10", "sides": "B/O", "qty": 250}, ["Black only", "qty 250"]),
    ({"key": "pull_up_banner", "qty": 2, "setup_artwork": True}, ["qty 2", "Setup / Artwork"]),
])
def test_details_for_different_products(client, flow, uploaded_file, payload, expected):
    status, _ = place(client, {**payload, "customer_name": "Test Co", "file_id": uploaded_file})
    assert status == 202
    for text in expected:
        assert text in flow[0]["json"]["details"]


def test_order_without_design_is_rejected(client, order, flow):
    payload = {k: v for k, v in order.items() if k != "file_id"}
    status, data = place(client, payload)
    assert status == 400
    assert "upload" in data["error"].lower()
    assert flow == []


@pytest.mark.parametrize("file_id", [
    "ffffffffffffffffffffffffffffffff.png",   # right format, but no such file
    "../app.py",                                # path traversal attempt
    "0123456789abcdef0123456789abcdef.exe",    # not an allowed type
    "design.png",                               # the customer's filename, not a storage key
    ["x"],
    123,
])
def test_order_with_unknown_or_malformed_design_is_rejected(client, order, flow, file_id):
    status, _ = place(client, {**order, "file_id": file_id})
    assert status == 400
    assert flow == []
