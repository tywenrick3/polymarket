"""Open-Meteo ensemble forecast client.

Fetches GFS ensemble (31 members) daily max temperature for a given
city and date. Uses the ensemble API's `temperature_2m_max` field
directly (not extracted from hourly data).

Forecasts are cached in the external_data table with a 1-hour TTL.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from polymarket_cli.cache import (
    get_connection,
    ensure_schema,
    store_external,
    get_external,
    is_fresh,
    mark_fetched,
)
from polymarket_cli.weather_cities import CityCoord

ENSEMBLE_BASE = "https://ensemble-api.open-meteo.com/v1/ensemble"
FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"

TTL_FORECAST = 3600  # 1 hour
DEFAULT_MODEL = "gfs_seamless"


@dataclass(frozen=True)
class EnsembleForecast:
    """Ensemble temperature forecast for a single day."""
    city_name: str
    target_date: date
    member_highs: list[float]   # daily max from each member (°F or °C)
    point_forecast: float       # deterministic best-guess daily max
    n_members: int
    unit: str                   # "fahrenheit" or "celsius"


async def fetch_ensemble_forecast(
    city: CityCoord,
    target_date: date,
    model: str = DEFAULT_MODEL,
) -> EnsembleForecast | None:
    """Fetch ensemble daily-max temperature forecast for a city and date.

    Returns None if the date is outside the forecast horizon (~16 days).
    """
    # --- Check cache ---
    conn = get_connection()
    ensure_schema(conn)

    cache_key = f"weather:ensemble:{city.name}:{target_date.isoformat()}"
    series_id = f"{city.name}:{target_date.isoformat()}"

    if is_fresh(conn, cache_key):
        cached = get_external(conn, "open_meteo_ensemble", series_id)
        if cached:
            highs = [p["value"] for p in cached]
            meta = cached[0].get("metadata", {})
            conn.close()
            return EnsembleForecast(
                city_name=city.name,
                target_date=target_date,
                member_highs=highs,
                point_forecast=meta.get("point_forecast", sum(highs) / len(highs)),
                n_members=len(highs),
                unit=city.unit,
            )

    # --- Forecast horizon check ---
    forecast_days = (target_date - date.today()).days + 1
    if forecast_days < 1 or forecast_days > 16:
        conn.close()
        return None

    fd = str(min(forecast_days + 1, 16))

    # --- Fetch ensemble + deterministic in parallel ---
    ens_params = {
        "latitude": str(city.lat),
        "longitude": str(city.lon),
        "daily": "temperature_2m_max",
        "models": model,
        "temperature_unit": city.unit,
        "timezone": city.timezone,
        "forecast_days": fd,
    }
    det_params = {
        "latitude": str(city.lat),
        "longitude": str(city.lon),
        "daily": "temperature_2m_max",
        "temperature_unit": city.unit,
        "timezone": city.timezone,
        "forecast_days": fd,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        ens_resp, det_resp = await asyncio.gather(
            client.get(ENSEMBLE_BASE, params=ens_params),
            client.get(FORECAST_BASE, params=det_params),
            return_exceptions=True,
        )

    # --- Parse ensemble members ---
    if isinstance(ens_resp, Exception):
        conn.close()
        return None

    try:
        ens_resp.raise_for_status()
        ens_data = ens_resp.json()
    except Exception:
        conn.close()
        return None

    member_highs = _extract_member_highs(ens_data, target_date)
    if not member_highs:
        conn.close()
        return None

    # --- Parse deterministic point forecast ---
    point_forecast: float | None = None
    if not isinstance(det_resp, Exception):
        try:
            det_resp.raise_for_status()
            point_forecast = _extract_point_forecast(det_resp.json(), target_date)
        except Exception:
            pass

    if point_forecast is None:
        point_forecast = sum(member_highs) / len(member_highs)

    # --- Cache ---
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cache_points = [
        {
            "timestamp": now_ts + i,
            "value": h,
            "metadata": {"member": i, "point_forecast": point_forecast, "model": model},
        }
        for i, h in enumerate(member_highs)
    ]
    store_external(conn, "open_meteo_ensemble", series_id, cache_points)
    mark_fetched(conn, cache_key, TTL_FORECAST)
    conn.close()

    return EnsembleForecast(
        city_name=city.name,
        target_date=target_date,
        member_highs=member_highs,
        point_forecast=point_forecast,
        n_members=len(member_highs),
        unit=city.unit,
    )


def _extract_member_highs(data: dict, target_date: date) -> list[float]:
    """Extract the daily max temperature for each ensemble member on target_date.

    The ensemble API returns daily data with keys like:
        temperature_2m_max_member01, temperature_2m_max_member02, ...
    """
    daily = data.get("daily", {})
    times = daily.get("time", [])

    target_str = target_date.isoformat()
    try:
        idx = times.index(target_str)
    except ValueError:
        return []

    member_keys = sorted(
        k for k in daily.keys()
        if k.startswith("temperature_2m_max_member")
    )

    if not member_keys:
        return []

    highs = []
    for key in member_keys:
        vals = daily[key]
        if idx < len(vals) and vals[idx] is not None:
            highs.append(vals[idx])

    return highs


def _extract_point_forecast(data: dict, target_date: date) -> float | None:
    """Extract the deterministic daily max from the standard forecast API."""
    daily = data.get("daily", {})
    times = daily.get("time", [])
    maxes = daily.get("temperature_2m_max", [])

    target_str = target_date.isoformat()
    try:
        idx = times.index(target_str)
    except ValueError:
        return None

    if idx < len(maxes) and maxes[idx] is not None:
        return maxes[idx]
    return None
