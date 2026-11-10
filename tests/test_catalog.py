"""Checks on products.json itself.

A typo or a wrong number in the catalog should fail CI, not show up on the live site.
"""
import pytest

from app import _HANDLERS, PRODUCTS

REQUIRED_FIELDS = {"category", "tagline", "pricing_type", "from_price", "icon"}
PURCHASABLE = [key for key, p in PRODUCTS.items() if p["pricing_type"] != "info_only"]


def price_tables(product):
    """Yield every quantity -> price table of a tiered product."""
    ptype = product["pricing_type"]
    if ptype == "tiered_multi_variant":
        for variant in product["variants"].values():
            if "sides_pricing" in variant:
                yield from variant["sides_pricing"].values()
            else:
                yield variant["combined_pricing"]
    elif ptype == "tiered_simple":
        if "sides_options" in product:
            for option in product["sides_options"].values():
                yield option["pricing"]
        else:
            yield product["qty_pricing"]


def all_prices(product):
    """Every price a customer can see for one tier, unit, set or option."""
    ptype = product["pricing_type"]
    if ptype in ("tiered_multi_variant", "tiered_simple"):
        return [price for table in price_tables(product) for price in table.values()]
    if ptype in ("fixed_qty_variant", "variant_fixed_price"):
        return [v["price"] for v in product["variants"].values()]
    if ptype == "per_unit_min_qty":
        return list(product["size_unit_price"].values())
    if ptype == "fixed_price":
        return [product["price"]]
    raise AssertionError(f"Unknown pricing type: {ptype}")


@pytest.mark.parametrize("key", PRODUCTS)
def test_product_has_required_fields(key):
    missing = REQUIRED_FIELDS - PRODUCTS[key].keys()
    assert not missing, f"{key} is missing {missing}"


@pytest.mark.parametrize("key", PURCHASABLE)
def test_pricing_type_has_a_handler(key):
    assert PRODUCTS[key]["pricing_type"] in _HANDLERS


@pytest.mark.parametrize("key", PURCHASABLE)
def test_all_prices_are_positive_numbers(key):
    for price in all_prices(PRODUCTS[key]):
        assert isinstance(price, (int, float)) and not isinstance(price, bool)
        assert price > 0


@pytest.mark.parametrize("key", PURCHASABLE)
def test_from_price_is_the_cheapest_price(key):
    # The "from $X" label on the home page must match what the product page can actually show.
    assert PRODUCTS[key]["from_price"] == min(all_prices(PRODUCTS[key]))


@pytest.mark.parametrize("key", PURCHASABLE)
def test_bigger_tiers_cost_more_in_total_but_less_per_unit(key):
    for table in price_tables(PRODUCTS[key]):
        tiers = sorted((int(qty), price) for qty, price in table.items())
        for (small_qty, small_price), (big_qty, big_price) in zip(tiers, tiers[1:]):
            assert big_price >= small_price, f"{key}: {big_qty} pcs cost less than {small_qty} pcs"
            assert big_price / big_qty <= small_price / small_qty, f"{key}: no volume discount at {big_qty}"


def test_setup_fee_is_a_positive_number():
    fee = PRODUCTS["pull_up_banner"]["addon_fee"]
    assert fee["amount"] > 0 and fee["key"]
