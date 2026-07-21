"""Ledger entry model and the helpers that build entries from raw records.

An entry is the atomic unit of the ledger. Every downstream module -- pricing,
reconciliation, reporting -- consumes entries produced here, so the invariants
documented on `LedgerEntry` are load-bearing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

# Entry kinds. `ADJUSTMENT` entries are corrections issued by finance and are
# deliberately excluded from gross-revenue style aggregates.
KIND_SALE = "sale"
KIND_REFUND = "refund"
KIND_ADJUSTMENT = "adjustment"
KIND_FEE = "fee"

ALL_KINDS = (KIND_SALE, KIND_REFUND, KIND_ADJUSTMENT, KIND_FEE)

# Kinds that reduce the amount owed to us rather than increase it.
NEGATIVE_KINDS = frozenset({KIND_REFUND, KIND_FEE})

CENTS = Decimal("0.01")


class LedgerError(Exception):
    """Base for every error raised out of the ledger package."""


class InvalidEntryError(LedgerError):
    """A raw record could not be turned into a valid entry."""


@dataclass
class LedgerEntry:
    """One posted ledger line.

    Invariants:
      * `amount` is always stored as a positive Decimal. Direction is carried by
        `kind`, never by the sign of the amount. Code that sums entries must
        consult `signed_amount`, not `amount`.
      * `amount` is tax-exclusive. Tax is applied later, in `pricing`.
      * `posted_at` is timezone-aware UTC.
    """

    entry_id: str
    kind: str
    amount: Decimal
    currency: str
    posted_at: datetime
    account_id: str
    memo: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.kind not in ALL_KINDS:
            raise InvalidEntryError(f"unknown entry kind: {self.kind!r}")
        if not isinstance(self.amount, Decimal):
            self.amount = Decimal(str(self.amount))
        if self.amount < 0:
            raise InvalidEntryError(
                f"entry {self.entry_id}: amount must be positive; "
                f"direction is carried by kind, got {self.amount}"
            )
        if self.posted_at.tzinfo is None:
            raise InvalidEntryError(f"entry {self.entry_id}: posted_at must be tz-aware")

    @property
    def signed_amount(self) -> Decimal:
        """The amount with its ledger direction applied.

        Refunds and fees reduce the balance; sales and adjustments increase it.
        """
        if self.kind in NEGATIVE_KINDS:
            return -self.amount
        return self.amount

    @property
    def is_adjustment(self) -> bool:
        return self.kind == KIND_ADJUSTMENT

    def quantized(self) -> Decimal:
        return self.amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def parse_amount(raw) -> Decimal:
    """Coerce a raw amount into a Decimal without going through float.

    Going via float here would reintroduce binary rounding error, which is the
    whole reason this ledger uses Decimal.
    """
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int,)):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(str(raw))
    if isinstance(raw, str):
        try:
            return Decimal(raw.strip().replace(",", ""))
        except Exception as exc:
            raise InvalidEntryError(f"cannot parse amount {raw!r}") from exc
    raise InvalidEntryError(f"unsupported amount type: {type(raw).__name__}")


def build_entry(record: dict) -> LedgerEntry:
    """Build a `LedgerEntry` from a raw dict record."""
    try:
        entry_id = str(record["id"])
        kind = str(record["kind"]).strip().lower()
        account_id = str(record["account_id"])
    except KeyError as exc:
        raise InvalidEntryError(f"record missing required field: {exc}") from exc

    amount = parse_amount(record.get("amount", 0))
    posted_raw = record.get("posted_at")
    if isinstance(posted_raw, datetime):
        posted_at = posted_raw
    elif isinstance(posted_raw, str):
        posted_at = datetime.fromisoformat(posted_raw)
    else:
        posted_at = datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)

    logger.debug("built entry %s kind=%s amount=%s", entry_id, kind, amount)
    return LedgerEntry(
        entry_id=entry_id,
        kind=kind,
        amount=amount,
        currency=str(record.get("currency", "USD")).upper(),
        posted_at=posted_at,
        account_id=account_id,
        memo=str(record.get("memo", "")),
        tags=list(record.get("tags", []) or []),
    )


def build_entries(records) -> list[LedgerEntry]:
    """Build many entries, skipping malformed records with a warning."""
    entries = []
    for record in records:
        try:
            entries.append(build_entry(record))
        except InvalidEntryError as exc:
            logger.warning("skipping malformed record: %s", exc)
    logger.debug("built %d entries", len(entries))
    return entries


def sort_by_posted(entries) -> list[LedgerEntry]:
    return sorted(entries, key=lambda e: (e.posted_at, e.entry_id))
