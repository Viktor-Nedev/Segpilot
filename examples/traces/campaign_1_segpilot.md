# Compression trace

### 1. 2127 → 432 tokens (ratio 20%)

**intent:** Run all tests to see failures and identify which tests are failing. (run_tests)

**original**

```
F...........FF.FFFF.F                                                    [100%]
================================== FAILURES ===================================
_______________ test_parse_amount_from_float_is_exact_to_cents ________________

    def test_parse_amount_from_float_is_exact_to_cents():
        """Parsing must not reintroduce binary float error.
    
        2.675 is the classic case: as a binary float it is actually
        2.67499999999999982..., so rounding the float to cents gives 2.67, while the
        intended decimal value rounds half-up to 2.68. Going through str preserves
        the intended value.
        """
        from decimal import ROUND_HALF_UP
    
        parsed = parse_amount(2.675)
>       assert parsed.quantize(CENTS, rounding=ROUND_HALF_UP) == Decimal("2.68")
E       AssertionError: assert Decimal('2.67') == Decimal('2.68')
E        +  where Decimal('2.67') = <built-in method quantize of decimal.Decimal object at 0x000002232B693F40>(Decimal('0.01'), r
  … [7332 more chars]
```

**compressed**

```
F...........FF.FFFF.F                                                    [100%]
[42 tests collected]
tests/test_entries.py::test_parse_amount_from_float_is_exact_to_cents FAILED
    assert parsed.quantize(CENTS, rounding=ROUND_HALF_UP) == Decimal("2.68")
E   AssertionError: assert Decimal('2.67') == Decimal('2.68')
tests/test_pricing.py::test_basket_applies_discount_before_tax FAILED
    assert priced["total"] == Decimal("193.05")
E   AssertionError: assert Decimal('214.50') == Decimal('193.05')
tests/test_reconcile.py::test_refunds_and_fees_reduce_the_balance FAILED
    assert reconcile_totals(entries) == Decimal("115.00")
E   AssertionError: assert Decimal('185.00') == Decimal('115.00')
tests/test_reconcile.py::test_account_balances_split_by_account FAILED
    assert balances["acct-1"] == Decimal("115.00")
E   AssertionError: assert Decimal('185.00') == Decimal('115.00')
tests/test_reconcile.py::test_no_discrepancies_when_settlement_agrees FAILED
    assert find_discrepancies(entries
  … [626 more chars]
```

### 2. 1708 → 930 tokens (ratio 54%)

**intent:** Read ledger/entries.py to understand the implementation of parse_amount and identify the source of the float parsing error. (reading ledger/entries.py)

**original**

```
     1	"""Ledger entry model and the helpers that build entries from raw records.
     2	
     3	An entry is the atomic unit of the ledger. Every downstream module -- pricing,
     4	reconciliation, reporting -- consumes entries produced here, so the invariants
     5	documented on `LedgerEntry` are load-bearing.
     6	"""
     7	
     8	from __future__ import annotations
     9	
    10	import logging
    11	from dataclasses import dataclass, field
    12	from datetime import datetime, timezone
    13	from decimal import Decimal, ROUND_HALF_UP
    14	
    15	logger = logging.getLogger(__name__)
    16	
    17	# Entry kinds. `ADJUSTMENT` entries are corrections issued by finance and are
    18	# deliberately excluded from gross-revenue style aggregates.
    19	KIND_SALE = "sale"
    20	KIND_REFUND = "refund"
    21	KIND_ADJUSTMENT = "adjustment"
    22	KIND_FEE = "fee"
    23	
    24	ALL_KINDS = (KIND_SALE, KIND_REFUND, KIND_ADJUSTMENT, KIND_FEE)
    25	
    26	# Kinds that reduce the
  … [5304 more chars]
```

**compressed**

```
[imports: logging, dataclass, field, datetime, Decimal, ROUND_HALF_UP]
logger = logging.getLogger(__name__)
KIND_SALE = "sale"
KIND_REFUND = "refund"
KIND_ADJUSTMENT = "adjustment"
KIND_FEE = "fee"
ALL_KINDS = (KIND_SALE, KIND_REFUND, KIND_ADJUSTMENT, KIND_FEE)
NEGATIVE_KINDS = frozenset({KIND_REFUND, KIND_FEE})
CENTS = Decimal("0.01")
class LedgerError(Exception):
    """Base for every error raised out of the ledger package."""
class InvalidEntryError(LedgerError):
    """A raw record could not be turned into a valid entry."""
@dataclass
class LedgerEntry:
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
        if
  … [2869 more chars]
```


---

**2 compressed, 0 skipped** — 3835 → 1362 tokens (saved 2473, overall ratio 36%)

