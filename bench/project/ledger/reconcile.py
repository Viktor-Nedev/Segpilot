"""Reconciliation between our ledger and an external settlement report.

This is the module finance looks at when the numbers disagree, so it favours
explicitness over cleverness.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from .entries import CENTS, KIND_ADJUSTMENT, LedgerEntry, LedgerError, sort_by_posted

logger = logging.getLogger(__name__)

# Amounts closer than this are treated as equal; below it we are into rounding
# noise from the settlement provider rather than genuine disagreement.
TOLERANCE = Decimal("0.01")


class ReconciliationError(LedgerError):
    """Raised when reconciliation cannot proceed at all."""


def reconcile_totals(entries, *, include_adjustments: bool = True) -> Decimal:
    """Sum entries into a single reconciled balance.

    Uses `signed_amount`, not `amount`: entry amounts are always stored positive
    and carry their direction in `kind`, so summing `amount` would make refunds
    and fees increase the balance instead of reducing it.
    """
    total = Decimal("0")
    for entry in entries:
        if entry.is_adjustment and not include_adjustments:
            logger.debug("skipping adjustment %s", entry.entry_id)
            continue
        total += entry.signed_amount
    return total.quantize(CENTS)


def group_by_account(entries) -> dict[str, list[LedgerEntry]]:
    grouped: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.account_id, []).append(entry)
    return {k: sort_by_posted(v) for k, v in grouped.items()}


def account_balances(entries, *, include_adjustments: bool = True) -> dict[str, Decimal]:
    """Reconciled balance per account."""
    return {
        account: reconcile_totals(rows, include_adjustments=include_adjustments)
        for account, rows in group_by_account(entries).items()
    }


def find_discrepancies(entries, settlement: dict, *, tolerance: Decimal = TOLERANCE):
    """Compare our per-account balances against a settlement report.

    Returns a list of (account_id, ours, theirs, delta) for accounts that differ
    by more than `tolerance`, plus accounts present on only one side.
    """
    ours = account_balances(entries)
    discrepancies = []

    for account, our_balance in sorted(ours.items()):
        their_balance = settlement.get(account)
        if their_balance is None:
            discrepancies.append((account, our_balance, None, our_balance))
            logger.warning("account %s missing from settlement", account)
            continue
        their_balance = Decimal(str(their_balance))
        delta = our_balance - their_balance
        if abs(delta) > tolerance:
            logger.warning(
                "discrepancy on %s: ours=%s theirs=%s delta=%s",
                account, our_balance, their_balance, delta,
            )
            discrepancies.append((account, our_balance, their_balance, delta))

    for account in sorted(set(settlement) - set(ours)):
        their_balance = Decimal(str(settlement[account]))
        discrepancies.append((account, None, their_balance, -their_balance))
        logger.warning("account %s missing from our ledger", account)

    return discrepancies


def reconciliation_summary(entries, settlement: dict) -> dict:
    """A one-shot summary suitable for the nightly report."""
    discrepancies = find_discrepancies(entries, settlement)
    adjustments = [e for e in entries if e.kind == KIND_ADJUSTMENT]
    return {
        "entry_count": len(list(entries)),
        "total": reconcile_totals(entries),
        "total_excluding_adjustments": reconcile_totals(
            entries, include_adjustments=False
        ),
        "adjustment_count": len(adjustments),
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
        "balanced": not discrepancies,
    }
