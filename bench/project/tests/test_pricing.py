"""Pricing tests: tax, discounts, and basket order-of-operations."""

from decimal import Decimal

import pytest

from ledger.entries import build_entries
from ledger.pricing import (
    DiscountPolicy,
    PricingError,
    apply_tax,
    price_basket,
    tax_rate_for,
)


def test_tax_rate_lookup_is_case_insensitive():
    assert tax_rate_for("us-ca") == Decimal("0.0725")
    assert tax_rate_for("GB") == Decimal("0.20")


def test_unknown_jurisdiction_falls_back_to_default():
    # US-CA is the default; an unknown code should price like it, not like 0.
    assert tax_rate_for("XX-ZZ") == Decimal("0.0725")


def test_apply_tax_adds_tax_not_replaces_amount():
    # 100 at 7.25% is 107.25, not 7.25.
    assert apply_tax(Decimal("100.00"), "US-CA") == Decimal("107.25")


def test_apply_tax_rejects_negative():
    with pytest.raises(PricingError):
        apply_tax(Decimal("-1.00"))


def test_discount_tier_boundary_is_inclusive():
    """A subtotal exactly at a tier threshold gets that tier's rate."""
    policy = DiscountPolicy(tiers=[(Decimal("100"), Decimal("0.10"))])
    assert policy.rate_for(Decimal("100")) == Decimal("0.10")
    assert policy.compute_discount(Decimal("100")) == Decimal("10.00")


def test_discount_tiers_evaluate_highest_first():
    """Tiers do not stack; the highest threshold met wins."""
    policy = DiscountPolicy(tiers=[
        (Decimal("100"), Decimal("0.05")),
        (Decimal("500"), Decimal("0.20")),
    ])
    assert policy.rate_for(Decimal("600")) == Decimal("0.20")
    assert policy.rate_for(Decimal("150")) == Decimal("0.05")


def test_basket_applies_discount_before_tax():
    """Order of operations finance signed off on: discount the subtotal, then
    tax the discounted amount. Taxing the pre-discount subtotal overcharges."""
    records = [
        {"id": "e1", "kind": "sale", "amount": "100.00", "account_id": "a", "posted_at": "2026-03-01T00:00:00+00:00"},
        {"id": "e2", "kind": "sale", "amount": "100.00", "account_id": "a", "posted_at": "2026-03-01T00:00:00+00:00"},
    ]
    entries = build_entries(records)
    policy = DiscountPolicy(tiers=[(Decimal("200"), Decimal("0.10"))])
    priced = price_basket(entries, jurisdiction="US-CA", discount_policy=policy)
    # subtotal 200 -> discount 20 -> taxable 180 -> +7.25% -> 193.05
    assert priced["subtotal"] == Decimal("200.00")
    assert priced["discount"] == Decimal("20.00")
    assert priced["taxable"] == Decimal("180.00")
    assert priced["total"] == Decimal("193.05")
