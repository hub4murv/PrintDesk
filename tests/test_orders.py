import app as app_module

ORDER = {
    "key": "business_cards",
    "variant": "standard_c2s_silk",
    "sides": "4/0",
    "qty": 100,
    "customer_name": "Test Co",
}


class FakeResponse:
    def raise_for_status(self):
        pass


def test_order_requires_customer_name(client):
    response = client.post("/api/order", json={**ORDER, "customer_name": ""})
    assert response.status_code == 400


def test_order_disabled_without_flow_url(client, monkeypatch):
    monkeypatch.setattr(app_module, "FLOW_ORDER_URL", None)
    response = client.post("/api/order", json=ORDER)
    assert response.status_code == 503


def test_order_sends_server_price_to_flow(client, monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(app_module, "FLOW_ORDER_URL", "https://example.test/flow")
    monkeypatch.setattr(app_module.requests, "post", fake_post)

    response = client.post("/api/order", json={**ORDER, "price": 1})

    assert response.status_code == 202
    expected = app_module.PRODUCTS["business_cards"]["variants"]["standard_c2s_silk"]["sides_pricing"]["4/0"]["100"]
    assert sent["payload"]["amount"] == expected