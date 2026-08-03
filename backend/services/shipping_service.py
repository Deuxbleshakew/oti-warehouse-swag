"""UPS Ground planning helpers used by both API validation and order output.

The transit-day defaults are a conservative, state-level transcription of the
UPS Ground outbound map supplied with this project. Transit time is calculated
automatically from the destination state and cannot be manually overridden.

Dates are planned in business days. Weekends and common observed U.S. federal
holidays are skipped. Carrier-specific closure dates can still vary, so the
calculated ship date is a planning deadline rather than a carrier guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


# Conservative state-level defaults based on the provided outbound map.
# States containing multiple map zones use the slower common zone so the plan
# errs toward shipping early. Exact ZIP/service information can override this.
UPS_GROUND_DAYS_BY_STATE = {
    # 1 business day
    "CT": 1, "DC": 1, "DE": 1, "MA": 1, "MD": 1, "NJ": 1,
    "NY": 1, "PA": 1, "RI": 1, "VT": 1,
    # 2 business days
    "GA": 2, "IN": 2, "KY": 2, "ME": 2, "MI": 2, "NC": 2,
    "NH": 2, "OH": 2, "SC": 2, "TN": 2, "VA": 2, "WV": 2,
    # 3 business days
    "AL": 3, "AR": 3, "AZ": 3, "FL": 3, "IA": 3, "IL": 3,
    "KS": 3, "LA": 3, "MN": 3, "MO": 3, "MS": 3, "NE": 3,
    "NM": 3, "WI": 3,
    # 4 business days
    "CO": 4, "MT": 4, "ND": 4, "OK": 4, "SD": 4, "TX": 4,
    # 5 business days
    "AK": 5, "CA": 5, "HI": 5, "ID": 5, "NV": 5, "OR": 5,
    "PR": 5, "UT": 5, "VI": 5, "WA": 5, "WY": 5,
}


class ShippingPlanError(ValueError):
    pass


@dataclass(frozen=True)
class ShippingPlan:
    event_date: str
    delivery_date: str
    ship_by_date: str
    ups_ground_days: int
    shipping_state: str
    shipping_service: str = "UPS Ground"

    def as_dict(self) -> dict:
        return {
            "event_date": self.event_date,
            "delivery_date": self.delivery_date,
            "ship_by_date": self.ship_by_date,
            "ups_ground_days": self.ups_ground_days,
            "shipping_state": self.shipping_state,
            "shipping_service": self.shipping_service,
        }


def normalize_state(value: str) -> str:
    state = (value or "").strip().upper()
    if state not in UPS_GROUND_DAYS_BY_STATE:
        raise ShippingPlanError("Choose a valid U.S. state or territory.")
    return state


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except (TypeError, ValueError):
        raise ShippingPlanError(f"{label} must be a valid date.")


def _observed_fixed(month: int, day: int, year: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:      # Saturday -> Friday
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:      # Sunday -> Monday
        return holiday + timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _federal_holidays(year: int) -> set[date]:
    # Common observed federal holidays. UPS may publish a slightly different
    # operating calendar, but this is safer than counting every weekday.
    return {
        _observed_fixed(1, 1, year),              # New Year's Day
        _nth_weekday(year, 1, 0, 3),              # MLK Day
        _nth_weekday(year, 2, 0, 3),              # Presidents Day
        _last_weekday(year, 5, 0),                # Memorial Day
        _observed_fixed(6, 19, year),             # Juneteenth
        _observed_fixed(7, 4, year),              # Independence Day
        _nth_weekday(year, 9, 0, 1),              # Labor Day
        _nth_weekday(year, 10, 0, 2),             # Columbus/Indigenous Day
        _observed_fixed(11, 11, year),            # Veterans Day
        _nth_weekday(year, 11, 3, 4),             # Thanksgiving
        _observed_fixed(12, 25, year),            # Christmas
    }


def is_business_day(value: date) -> bool:
    if value.weekday() >= 5:
        return False
    holidays = set()
    # Observed New Year's Day can fall in the adjacent calendar year.
    for y in (value.year - 1, value.year, value.year + 1):
        holidays.update(_federal_holidays(y))
    return value not in holidays


def previous_business_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while not is_business_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def subtract_business_days(value: date, days: int) -> date:
    candidate = value
    remaining = int(days)
    while remaining > 0:
        candidate -= timedelta(days=1)
        if is_business_day(candidate):
            remaining -= 1
    return candidate


def build_shipping_plan(*, event_date: str, shipping_state: str,
                        ups_ground_days: Optional[int] = None) -> ShippingPlan:
    # ups_ground_days remains in the signature for backward compatibility with
    # older clients, but the authoritative value always comes from the map.
    event = _parse_iso_date(event_date, "Event date")
    state = normalize_state(shipping_state)
    transit_days = UPS_GROUND_DAYS_BY_STATE[state]

    delivery = previous_business_day(event)
    ship_by = subtract_business_days(delivery, transit_days)
    return ShippingPlan(
        event_date=event.isoformat(),
        delivery_date=delivery.isoformat(),
        ship_by_date=ship_by.isoformat(),
        ups_ground_days=transit_days,
        shipping_state=state,
    )
