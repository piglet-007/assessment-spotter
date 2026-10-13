"""Hourly forecasts from Open-Meteo, looked up at the time the truck arrives.

Callers snap locations to a grid (0.1 degree is about the forecast models' own
resolution), so nearby points on different routes share one cached forecast.
"""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from django.core.cache import cache

from .errors import ProviderError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES = ("wind_speed_10m", "wind_gusts_10m", "rain", "snowfall")
CELL_DEG = 0.1
BATCH_SIZE = 100
PARALLEL_REQUESTS = 4
CACHE_SECONDS = 3600
FORECAST_HORIZON_DAYS = 15  # Open-Meteo serves 16 days including today
# Every fetch covers at least this long after its start, so route checkpoints and
# the 48 hour heatmap for the same departure share cache entries.
MIN_WINDOW = timedelta(hours=48)


def snap(lat, lon, cell_deg=CELL_DEG):
    """Centre of the grid cell containing (lat, lon). Equal inputs give equal keys."""
    return (round(round(lat / cell_deg) * cell_deg, 4), round(round(lon / cell_deg) * cell_deg, 4))


@dataclass(frozen=True)
class Conditions:
    wind_mph: float
    gust_mph: float
    rain_in_hr: float
    snow_in_hr: float

    def to_dict(self):
        return {
            "wind_mph": round(self.wind_mph, 1),
            "gust_mph": round(self.gust_mph, 1),
            "rain_in_hr": round(self.rain_in_hr, 3),
            "snow_in_hr": round(self.snow_in_hr, 3),
        }


@dataclass(frozen=True)
class HourlySeries:
    start: datetime  # aware UTC datetime of the first value, on the hour
    wind: tuple
    gust: tuple
    rain: tuple
    snow: tuple

    def at(self, when):
        """Conditions at an arbitrary moment, or None if outside the series or missing.

        Wind is an instantaneous value, so it is interpolated between hours.
        Rain and snow are totals for the hour *ending* at each timestamp, so the
        rate at 13:20 is the 14:00 value.
        """
        h = (when - self.start).total_seconds() / 3600
        lo, hi = math.floor(h), math.ceil(h)
        if lo < 0 or hi >= len(self.wind):
            return None
        values = (self.wind[lo], self.wind[hi], self.gust[lo], self.gust[hi], self.rain[hi], self.snow[hi])
        if any(v is None for v in values):
            return None
        frac = h - lo
        return Conditions(
            wind_mph=self.wind[lo] + (self.wind[hi] - self.wind[lo]) * frac,
            gust_mph=self.gust[lo] + (self.gust[hi] - self.gust[lo]) * frac,
            rain_in_hr=self.rain[hi],
            snow_in_hr=self.snow[hi],
        )


def parse_series(location):
    hourly = location["hourly"]
    start = datetime.fromisoformat(hourly["time"][0]).replace(tzinfo=timezone.utc)
    return HourlySeries(
        start=start,
        wind=tuple(hourly["wind_speed_10m"]),
        gust=tuple(hourly["wind_gusts_10m"]),
        rain=tuple(hourly["rain"]),
        snow=tuple(hourly["snowfall"]),
    )


class OpenMeteoClient:
    def __init__(self, session=None, timeout=30):
        self.session = session or requests.Session()
        self.timeout = timeout

    def series(self, points, start, end, today=None):
        """Hourly series covering [start, end] for each snapped (lat, lon) point.

        Points whose window lies beyond the forecast horizon are left out, and
        callers treat them as unknown weather.
        """
        today = today or datetime.now(timezone.utc).date()
        horizon = today + timedelta(days=FORECAST_HORIZON_DAYS)
        end = max(end, start + MIN_WINDOW)
        start_date = start.astimezone(timezone.utc).date()
        end_date = min(end.astimezone(timezone.utc).date(), horizon)
        if start_date > horizon:
            return {}

        found, missing = {}, []
        for point in dict.fromkeys(points):
            cached = cache.get(self._cache_key(point, start_date, end_date))
            if cached is None:
                missing.append(point)
            else:
                found[point] = cached

        batches = [missing[i : i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
        with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as pool:
            fetched = pool.map(lambda batch: self._fetch(batch, start_date, end_date), batches)
            for batch, results in zip(batches, fetched):
                for point, series in zip(batch, results):
                    cache.set(self._cache_key(point, start_date, end_date), series, CACHE_SECONDS)
                    found[point] = series
        return found

    @staticmethod
    def _cache_key(point, start_date, end_date):
        return f"om:{point[0]:.4f}:{point[1]:.4f}:{start_date}:{end_date}"

    def _fetch(self, points, start_date, end_date):
        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
            "hourly": ",".join(HOURLY_VARIABLES),
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        try:
            response = self.session.get(FORECAST_URL, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"Could not reach Open-Meteo: {exc.__class__.__name__}") from exc
        if response.status_code == 429:
            raise ProviderError("The weather service is busy (rate limit reached). Try again in a minute.")
        if not response.ok:
            try:
                detail = response.json().get("reason", "")
            except ValueError:
                detail = ""
            raise ProviderError(f"Weather request failed ({response.status_code}) {detail}".strip())

        data = response.json()
        locations = data if isinstance(data, list) else [data]  # a single location is not wrapped
        return [parse_series(location) for location in locations]
