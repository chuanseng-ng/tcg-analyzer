"""Published grading-company reference data: rules versions and service options.

Spec §23 requires published rules to be stored separately from learned model
behaviour, versioned, and never overwritten — a historical analysis retains the
rules version it used, which is what §57's ``grading_rules_version`` records.
:class:`GradingRules` is that record's shape; #47 persists it.

**The rules body is deliberately empty in V1.** Each company's grading standard
is copyrighted text belonging to that company, and this repository does not
reproduce it. What the product needs is the *identifier* — so that an analysis
run today can be told apart from one run after a company revises its standard —
plus a pointer to the source a human can read. Both are here. A future
milestone that genuinely needs machine-readable tolerances adds them under a new
version rather than editing one.

:class:`ServiceOption` is spec §22's ``get_service_options()`` return type. It
is constructed nowhere in this package: fees are *configurable* economic inputs
(§45 — "configurable grading fee", "configurable shipping", "configurable
insurance"), so the numbers belong to M5's economic configuration where a user
can change them, not to a hard-coded table here that would go stale quarterly
and disagree with whatever M5 was actually told. The shape exists so that M5
fills a contract instead of inventing one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from tcg_domain.money import Money

__all__ = ["EMPTY_RULES", "GradingRules", "ServiceOption"]

#: The rules body every V1 adapter carries — see the module docstring. A shared
#: read-only mapping rather than a fresh `{}` per record, so nothing can mutate
#: one company's rules through another's.
EMPTY_RULES: Mapping[str, object] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class GradingRules:
    """One version of one company's published grading standard (spec §23).

    Args:
        company: The company's lowercase slug.
        version: The identifier a historical analysis retains. Never reused and
            never edited — a revised standard is a new record.
        effective_from: When the company's published standard took effect, as
            far as it states one.
        effective_to: When it stopped applying, or `None` while it is current.
        source: Where the standard was read. A URL a human can open.
        verified_on: When it was last read. ADR 0006's ninety-day rule applies
            to this reference data too: a finding older than ninety days is
            re-read before anything relies on it.
        rules: Machine-readable tolerances. Empty in V1; see the module
            docstring for why that is a decision rather than a gap.
    """

    company: str
    version: str
    effective_from: date | None
    source: str
    verified_on: date
    effective_to: date | None = None
    rules: Mapping[str, object] = field(default=EMPTY_RULES)

    def __str__(self) -> str:
        return f"{self.company} {self.version}"


@dataclass(frozen=True, slots=True)
class ServiceOption:
    """One submission tier a company offers (spec §22, §47).

    Args:
        tier: The company's own name for the tier.
        fee: What the company charges per card, in the currency the company
            bills in. No conversion happens here — spec §46 makes the product's
            figures SGD, and turning a USD fee into one is the economic
            engine's job, with the rate it used recorded alongside the result.
        max_declared_value: The ceiling on a card's declared value for this
            tier, or `None` where the company sets none.
        turnaround_business_days: The company's published turnaround, or `None`
            where it publishes none.
    """

    tier: str
    fee: Money
    max_declared_value: Money | None = None
    turnaround_business_days: int | None = None

    def __str__(self) -> str:
        return f"{self.tier} ({self.fee})"
