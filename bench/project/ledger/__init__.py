"""A small double-entry-ish ledger used as the benchmark codebase.

Modules:
  entries    -- the LedgerEntry model and record parsing
  pricing    -- tax and discount application
  reconcile  -- reconciliation against an external settlement report
"""

from .entries import (
    ALL_KINDS,
    KIND_ADJUSTMENT,
    KIND_FEE,
    KIND_REFUND,
    KIND_SALE,
    InvalidEntryError,
    LedgerEntry,
    LedgerError,
    build_entries,
    build_entry,
)
from .pricing import DiscountPolicy, PricingError, apply_tax, price_basket
from .reconcile import (
    ReconciliationError,
    account_balances,
    find_discrepancies,
    reconcile_totals,
    reconciliation_summary,
)

__all__ = [
    "ALL_KINDS", "KIND_ADJUSTMENT", "KIND_FEE", "KIND_REFUND", "KIND_SALE",
    "InvalidEntryError", "LedgerEntry", "LedgerError", "build_entries", "build_entry",
    "DiscountPolicy", "PricingError", "apply_tax", "price_basket",
    "ReconciliationError", "account_balances", "find_discrepancies",
    "reconcile_totals", "reconciliation_summary",
]
