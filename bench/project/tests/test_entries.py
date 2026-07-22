"""Entry model tests: parsing, precision, quantization, defaults."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ledger.entries import (
    CENTS,
    InvalidEntryError,
    LedgerEntry,
    build_entry,
    parse_amount,
)
from ledger.pricing import DEFAULT_JURISDICTION, apply_tax

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_parse_amount_from_float_is_exact_to_cents():
    """Parsing must not reintroduce binary float error.

    2.675 is the classic case: as a binary float it is actually
    2.67499999999999982..., so rounding the float to cents gives 2.67, while the
    intended decimal value rounds half-up to 2.68. Going through str preserves
    the intended value.
    """
    from decimal import ROUND_HALF_UP

    parsed = parse_amount(2.675)
    assert parsed.quantize(CENTS, rounding=ROUND_HALF_UP) == Decimal("2.68")


def test_parse_amount_from_string_and_int():
    assert parse_amount("1,234.50") == Decimal("1234.50")
    assert parse_amount(42) == Decimal("42")


def test_cents_is_two_decimal_places():
    """Money quantizes to cents. If CENTS drifts, every rounded amount is wrong."""
    assert CENTS == Decimal("0.01")
    entry = LedgerEntry("e1", "sale", Decimal("10.005"), "USD", NOW, "a")
    assert entry.quantized() == Decimal("10.01")   # half-up at the cent


def test_default_jurisdiction_is_taxed():
    """The default jurisdiction must be a real, taxed one; a 0% default would
    silently undercharge every basket that does not name a jurisdiction."""
    assert DEFAULT_JURISDICTION == "US-CA"
    assert apply_tax(Decimal("100.00")) == Decimal("107.25")


def test_amount_must_be_positive():
    with pytest.raises(InvalidEntryError):
        LedgerEntry("e1", "sale", Decimal("-5"), "USD", NOW, "a")


def test_build_entry_normalizes_naive_datetime_to_utc():
    entry = build_entry({"id": "e1", "kind": "sale", "amount": "5",
                         "account_id": "a", "posted_at": "2026-03-01T00:00:00"})
    assert entry.posted_at.tzinfo is not None
