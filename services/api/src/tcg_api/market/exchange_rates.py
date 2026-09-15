"""Recording an exchange rate into SGD — `tcg-record-exchange-rate` (issue #53).

ADR 0006's provider prices in USD and V1 reports SGD, so normalization converts,
and a converted price is reproducible only if the rate it used is on file. **No
FX feed exists, deliberately**: one would be a new external provider, needing
its own ADR from the rubric in `docs/market-provider-research.md`, and the
application has no route out. An operator records the rate instead, with the URL
it was read from.

A rate is append-only (`trg_exchange_rates_immutable`): a rewritten rate would
silently reprice every observation that names it. One per currency per day
(`uq_exchange_rates_base_currency_quote_currency_as_of`); a wrong rate that no
observation has used yet is corrected by deleting it, never by editing it.

    uv run tcg-record-exchange-rate --base USD --rate 1.3421 \\
      --as-of 2026-09-15 --source https://www.mas.gov.sg/statistics/exchange-rates
"""

from __future__ import annotations

import argparse
import asyncio
import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Final

import sqlalchemy as sa
import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.logging import configure_logging
from tcg_api.market.tables import exchange_rates

__all__ = ["ExchangeRateRefused", "main", "record_exchange_rate", "verify_rate"]

logger = structlog.get_logger(__name__)

_CURRENCY: Final = re.compile(r"^[A-Z]{3}$")

#: `exchange_rates.rate` is NUMERIC(18, 8).
_RATE_PLACES: Final = 8

_ONE_PER_DAY: Final = "uq_exchange_rates_base_currency_quote_currency_as_of"


class ExchangeRateRefused(ValueError):
    """A rate this project will not record, in words an operator can act on."""


def verify_rate(*, base_currency: str, rate: Decimal) -> None:
    """Refuse a rate the database would refuse, or would silently round.

    Raises:
        ExchangeRateRefused: If `base_currency` is not an ISO 4217 code or is
            SGD, or `rate` is not a positive finite decimal of at most eight
            places.
    """
    if not _CURRENCY.match(base_currency):
        raise ExchangeRateRefused(f"{base_currency!r} is not an ISO 4217 code such as 'USD'")
    if base_currency == "SGD":
        raise ExchangeRateRefused(
            "an SGD quote is stored unconverted, so an SGD-to-SGD rate is one nothing may use"
        )
    if not rate.is_finite() or rate <= 0:
        raise ExchangeRateRefused(f"a rate must be a positive, finite number; got {rate}")
    exponent = rate.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -_RATE_PLACES and rate != round(rate, _RATE_PLACES):
        raise ExchangeRateRefused(
            f"{rate} has more than {_RATE_PLACES} decimal places, and would be stored rounded "
            "into a rate nobody published"
        )


async def record_exchange_rate(
    connection: AsyncConnection,
    *,
    base_currency: str,
    rate: Decimal,
    as_of: date,
    source_reference: str,
) -> uuid.UUID:
    """Write one rate into SGD in the caller's transaction, and return its id.

    Raises:
        ExchangeRateRefused: If `verify_rate` refuses it or the source is empty.
        IntegrityError: If a rate for this currency and day is already recorded.
    """
    verify_rate(base_currency=base_currency, rate=rate)
    if not source_reference.strip():
        raise ExchangeRateRefused("a rate needs the URL it was read from; one with none is a guess")
    rate_id = uuid.uuid4()
    await connection.execute(
        sa.insert(exchange_rates).values(
            id=rate_id,
            base_currency=base_currency,
            quote_currency="SGD",
            rate=rate,
            as_of=as_of,
            source_reference=source_reference,
        )
    )
    return rate_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("--base", required=True, help="the currency converted from — 'USD'")
    parser.add_argument(
        "--rate", required=True, type=Decimal, help="SGD per one unit of --base — '1.3421'"
    )
    parser.add_argument(
        "--as-of", required=True, type=date.fromisoformat, help="the day it applies to, ISO 8601"
    )
    parser.add_argument("--source", required=True, help="the URL the rate was read from")
    return parser


async def _run(arguments: argparse.Namespace) -> uuid.UUID:
    engine = create_engine()
    try:
        async with engine.begin() as connection:
            return await record_exchange_rate(
                connection,
                base_currency=arguments.base,
                rate=arguments.rate,
                as_of=arguments.as_of,
                source_reference=arguments.source,
            )
    finally:
        await engine.dispose()


def main() -> int:
    """Console-script entry point (`uv run tcg-record-exchange-rate`)."""
    parser = _parser()
    arguments = parser.parse_args()
    try:
        verify_rate(base_currency=arguments.base, rate=arguments.rate)
    except ExchangeRateRefused as refusal:
        parser.error(str(refusal))

    configure_logging(get_settings())

    try:
        rate_id = asyncio.run(_run(arguments))
    except ExchangeRateRefused as refusal:
        logger.error("market.exchange_rate_refused", reason=str(refusal))
        return 1
    except IntegrityError as conflict:
        if _ONE_PER_DAY in str(conflict.orig):
            logger.error(
                "market.exchange_rate_already_recorded",
                base_currency=arguments.base,
                as_of=arguments.as_of.isoformat(),
            )
        else:
            logger.error("market.exchange_rate_refused_by_the_database", error=str(conflict.orig))
        return 1

    logger.info(
        "market.exchange_rate_recorded",
        exchange_rate_id=str(rate_id),
        base_currency=arguments.base,
        rate=str(arguments.rate),
        as_of=arguments.as_of.isoformat(),
    )
    return 0
