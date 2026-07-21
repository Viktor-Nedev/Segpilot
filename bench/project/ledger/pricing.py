"""Tax and discount application.

Entries arrive tax-exclusive from `entries`. Everything that turns a raw entry
amount into a customer-facing number goes through here, so that rounding happens
in exactly one place.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from .entries import CENTS, LedgerEntry, LedgerError

logger = logging.getLogger(__name__)

# Jurisdiction -> tax rate. Rates are fractions, not percentages.
TAX_RATES: dict[str, Decimal] = {
    "US-CA": Decimal("0.0725"),
    "US-NY": Decimal("0.08875"),
    "US-OR": Decimal("0.0"),
    "GB": Decimal("0.20"),
    "DE": Decimal("0.19"),
    "BG": Decimal("0.20"),
}

DEFAULT_JURISDICTION = "US-CA"


class PricingError(LedgerError):
    """Raised when a price cannot be computed."""


class DiscountPolicy:
    """A tiered percentage discount.

    Tiers are (threshold, rate) pairs, evaluated from the highest threshold
    down: the first tier whose threshold the subtotal meets or exceeds wins.
    Tiers do not stack.
    """

    def __init__(self, tiers=None, *, max_rate=Decimal("0.5")):
        self.tiers = sorted(tiers or [], key=lambda t: t[0], reverse=True)
        self.max_rate = max_rate

    def rate_for(self, subtotal: Decimal) -> Decimal:
        for threshold, rate in self.tiers:
            if subtotal >= threshold:
                capped = min(Decimal(str(rate)), self.max_rate)
                logger.debug("discount tier %s -> rate %s", threshold, capped)
                return capped
        return Decimal("0")

    def compute_discount(self, subtotal: Decimal) -> Decimal:
        if subtotal <= 0:
            return Decimal("0")
        discount = subtotal * self.rate_for(subtotal)
        return discount.quantize(CENTS, rounding=ROUND_HALF_UP)


def tax_rate_for(jurisdiction: str) -> Decimal:
    """Look up a tax rate, falling back to the default jurisdiction."""
    key = (jurisdiction or "").strip().upper()
    if key in TAX_RATES:
        return TAX_RATES[key]
    logger.warning("unknown jurisdiction %r, falling back to %s", jurisdiction,
                   DEFAULT_JURISDICTION)
    return TAX_RATES[DEFAULT_JURISDICTION]


def apply_tax(amount: Decimal, jurisdiction: str = DEFAULT_JURISDICTION) -> Decimal:
    """Add tax to a tax-exclusive amount."""
    if amount < 0:
        raise PricingError(f"cannot tax a negative amount: {amount}")
    rate = tax_rate_for(jurisdiction)
    taxed = amount * (Decimal("1") + rate)
    return taxed.quantize(CENTS, rounding=ROUND_HALF_UP)


def entry_gross(entry: LedgerEntry, jurisdiction: str = DEFAULT_JURISDICTION) -> Decimal:
    """The tax-inclusive amount for a single entry, sign applied.

    Note the sign is applied AFTER tax, so a refund is refunded gross.
    """
    taxed = apply_tax(entry.amount, jurisdiction)
    return -taxed if entry.signed_amount < 0 else taxed


def price_basket(
    entries,
    *,
    jurisdiction: str = DEFAULT_JURISDICTION,
    discount_policy: DiscountPolicy | None = None,
) -> dict:
    """Price a basket of entries.

    Order of operations, which finance signed off on:
      1. sum tax-exclusive amounts
      2. apply the discount to that subtotal
      3. apply tax to the discounted subtotal
    """
    subtotal = sum((e.amount for e in entries), Decimal("0"))
    discount = (
        discount_policy.compute_discount(subtotal) if discount_policy else Decimal("0")
    )
    discounted = subtotal - discount
    if discounted < 0:
        raise PricingError(
            f"discount {discount} exceeds subtotal {subtotal}"
        )
    total = apply_tax(discounted, jurisdiction)
    logger.debug(
        "priced basket: subtotal=%s discount=%s total=%s", subtotal, discount, total
    )
    return {
        "subtotal": subtotal.quantize(CENTS, rounding=ROUND_HALF_UP),
        "discount": discount,
        "taxable": discounted.quantize(CENTS, rounding=ROUND_HALF_UP),
        "total": total,
        "jurisdiction": jurisdiction,
    }
