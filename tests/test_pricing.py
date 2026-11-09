import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"PrintDesk" in response.data


def test_business_cards_price(client):
    response = client.post("/api/price", json={
        "key": "business_cards",
        "variant": "standard_c2s_silk",
        "sides": "4/0",
        "qty": 100,
    })
    assert response.status_code == 200
    assert response.get_json()["price"] == 39

def test_posters_price(client):
    response = client.post("/api/price", json={
        "key": "posters",
        "size": "8.5x11",
        "qty": 20,
    })
    assert response.status_code == 200
    assert response.get_json()["price"] == 25

def test_posters_below_minimum(client):
    response = client.post("/api/price", json={
        "key": "posters",
        "size": "8.5x11",
        "qty": 10,
    })
    assert response.status_code == 400

def test_unknown_product_is_rejected(client):
    response = client.post("/api/price", json={"key": "does_not_exist"})
    assert response.status_code == 400