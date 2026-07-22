"""Reconciliation tests."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ledger import build_entries, reconcile_totals
from ledger.reconcile import account_balances, find_discrepancies, reconciliation_summary

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _records():
    return [
        {"id": "e1", "kind": "sale", "amount": "100.00", "account_id": "acct-1", "posted_at": NOW},
        {"id": "e2", "kind": "sale", "amount": "50.00", "account_id": "acct-1", "posted_at": NOW},
        {"id": "e3", "kind": "refund", "amount": "30.00", "account_id": "acct-1", "posted_at": NOW},
        {"id": "e4", "kind": "fee", "amount": "5.00", "account_id": "acct-1", "posted_at": NOW},
        {"id": "e5", "kind": "sale", "amount": "200.00", "account_id": "acct-2", "posted_at": NOW},
        {"id": "e6", "kind": "adjustment", "amount": "10.00", "account_id": "acct-2", "posted_at": NOW},
    ]


def test_refunds_and_fees_reduce_the_balance():
    """100 + 50 - 30 - 5 = 115.

    Entry amounts are stored positive; direction comes from `kind`. Summing
    `amount` instead of `signed_amount` would give 185 here.
    """
    entries = [e for e in build_entries(_records()) if e.account_id == "acct-1"]
    assert reconcile_totals(entries) == Decimal("115.00")


def test_adjustments_can_be_excluded():
    entries = [e for e in build_entries(_records()) if e.account_id == "acct-2"]
    assert reconcile_totals(entries) == Decimal("210.00")
    assert reconcile_totals(entries, include_adjustments=False) == Decimal("200.00")


def test_account_balances_split_by_account():
    balances = account_balances(build_entries(_records()))
    assert balances["acct-1"] == Decimal("115.00")
    assert balances["acct-2"] == Decimal("210.00")


def test_no_discrepancies_when_settlement_agrees():
    entries = build_entries(_records())
    settlement = {"acct-1": "115.00", "acct-2": "210.00"}
    assert find_discrepancies(entries, settlement) == []


def test_discrepancy_is_reported_with_delta():
    entries = build_entries(_records())
    settlement = {"acct-1": "100.00", "acct-2": "210.00"}
    found = find_discrepancies(entries, settlement)
    assert len(found) == 1
    account, ours, theirs, delta = found[0]
    assert account == "acct-1"
    assert delta == Decimal("15.00")


def test_discrepancy_reported_when_settlement_is_higher_than_ours():
    """A negative delta (ours < theirs) must be flagged, not just a positive one.

    Comparing the raw delta instead of its absolute value silently ignores every
    case where the settlement provider thinks we are owed more than we recorded.
    """
    entries = build_entries(_records())
    settlement = {"acct-1": "150.00", "acct-2": "210.00"}   # acct-1 ours=115
    found = find_discrepancies(entries, settlement)
    accounts = {row[0]: row for row in found}
    assert "acct-1" in accounts
    _, ours, theirs, delta = accounts["acct-1"]
    assert delta == Decimal("-35.00")


def test_missing_account_on_either_side_is_flagged():
    entries = build_entries(_records())
    found = find_discrepancies(entries, {"acct-1": "115.00", "acct-9": "42.00"})
    accounts = {row[0] for row in found}
    assert "acct-2" in accounts      # missing from settlement
    assert "acct-9" in accounts      # missing from our ledger


def test_summary_reports_balanced():
    entries = build_entries(_records())
    summary = reconciliation_summary(entries, {"acct-1": "115.00", "acct-2": "210.00"})
    assert summary["balanced"] is True
    assert summary["entry_count"] == 6
    assert summary["adjustment_count"] == 1
    assert summary["total"] == Decimal("325.00")
    assert summary["total_excluding_adjustments"] == Decimal("315.00")
