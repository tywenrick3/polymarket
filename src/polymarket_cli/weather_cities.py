"""City coordinate mapping and Polymarket weather event title parsing.

Polymarket weather titles look like:
    "Highest temperature in NYC on March 18?"
    "Highest temperature in Buenos Aires on March 17?"

Each event has ~11 bracket markets as group outcomes like:
    "32-33°F"  "34-35°F"  "31°F or below"  (US cities, Fahrenheit, 2°F ranges)
    "9°C"  "10°C"  "13°C or below"          (non-US cities, Celsius, 1°C bins)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CityCoord:
    name: str
    lat: float
    lon: float
    timezone: str
    unit: str  # "fahrenheit" or "celsius"


# Coordinates target NOAA / WMO observation stations where possible,
# since official observations are the resolution source.
CITY_DB: dict[str, CityCoord] = {
    # --- US cities (Fahrenheit, 2°F brackets) ---
    "nyc":            CityCoord("NYC",            40.7789, -73.9692, "America/New_York",     "fahrenheit"),
    "new york":       CityCoord("NYC",            40.7789, -73.9692, "America/New_York",     "fahrenheit"),
    "new york city":  CityCoord("NYC",            40.7789, -73.9692, "America/New_York",     "fahrenheit"),
    "chicago":        CityCoord("Chicago",        41.9742, -87.9073, "America/Chicago",      "fahrenheit"),
    "miami":          CityCoord("Miami",          25.7907, -80.3164, "America/New_York",     "fahrenheit"),
    "dallas":         CityCoord("Dallas",         32.8998, -97.0403, "America/Chicago",      "fahrenheit"),
    "seattle":        CityCoord("Seattle",        47.4489, -122.309, "America/Los_Angeles",  "fahrenheit"),
    "atlanta":        CityCoord("Atlanta",        33.6407, -84.4277, "America/New_York",     "fahrenheit"),
    # --- International cities (Celsius, 1°C brackets) ---
    "london":         CityCoord("London",         51.4700, -0.4543,  "Europe/London",        "celsius"),
    "paris":          CityCoord("Paris",          48.7262,  2.3592,  "Europe/Paris",         "celsius"),
    "munich":         CityCoord("Munich",         48.3537, 11.7750,  "Europe/Berlin",        "celsius"),
    "warsaw":         CityCoord("Warsaw",         52.1657, 20.9671,  "Europe/Warsaw",        "celsius"),
    "ankara":         CityCoord("Ankara",         40.1281, 32.9951,  "Europe/Istanbul",      "celsius"),
    "tel aviv":       CityCoord("Tel Aviv",       32.0055, 34.8854,  "Asia/Jerusalem",       "celsius"),
    "seoul":          CityCoord("Seoul",          37.5600, 126.800,  "Asia/Seoul",           "celsius"),
    "tokyo":          CityCoord("Tokyo",          35.5533, 139.781,  "Asia/Tokyo",           "celsius"),
    "shanghai":       CityCoord("Shanghai",       31.1434, 121.805,  "Asia/Shanghai",        "celsius"),
    "hong kong":      CityCoord("Hong Kong",      22.3089, 113.915,  "Asia/Hong_Kong",       "celsius"),
    "singapore":      CityCoord("Singapore",       1.3502, 103.994,  "Asia/Singapore",       "celsius"),
    "taipei":         CityCoord("Taipei",         25.0777, 121.224,  "Asia/Taipei",          "celsius"),
    "lucknow":        CityCoord("Lucknow",        26.8467, 80.9462,  "Asia/Kolkata",         "celsius"),
    "toronto":        CityCoord("Toronto",        43.6777, -79.6248, "America/Toronto",      "celsius"),
    "buenos aires":   CityCoord("Buenos Aires",  -34.5592, -58.4156, "America/Argentina/Buenos_Aires", "celsius"),
    "sao paulo":      CityCoord("Sao Paulo",     -23.6273, -46.6566, "America/Sao_Paulo",    "celsius"),
    "são paulo":      CityCoord("Sao Paulo",     -23.6273, -46.6566, "America/Sao_Paulo",    "celsius"),
    "wellington":     CityCoord("Wellington",     -41.3272, 174.805,  "Pacific/Auckland",     "celsius"),
    "madrid":         CityCoord("Madrid",         40.4719, -3.5626,  "Europe/Madrid",        "celsius"),
    "milan":          CityCoord("Milan",          45.6301,  8.7231,  "Europe/Rome",          "celsius"),
}

# ---------------------------------------------------------------------------
# Title parsing
# ---------------------------------------------------------------------------

# "Highest temperature in Tel Aviv on March 18?"
_TITLE_RE = re.compile(
    r"^Highest\s+temperature\s+in\s+(?P<city>.+?)\s+on\s+(?P<month>\w+)\s+(?P<day>\d{1,2})\??$",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_weather_event(title: str, current_year: int) -> tuple[CityCoord, date] | None:
    """Parse a Polymarket weather event title into (city, target_date).

    Returns None if the title doesn't match or the city is unknown.
    """
    m = _TITLE_RE.match(title.strip())
    if not m:
        return None

    city_key = m.group("city").strip().lower()
    city = CITY_DB.get(city_key)
    if city is None:
        return None

    month_name = m.group("month").lower()
    month_num = _MONTHS.get(month_name)
    if month_num is None:
        return None

    day = int(m.group("day"))
    try:
        target = date(current_year, month_num, day)
    except ValueError:
        return None

    return city, target


def is_weather_event(title: str) -> bool:
    """Quick check: does this title look like a temperature market?"""
    return bool(_TITLE_RE.match(title.strip()))
