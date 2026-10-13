from datetime import datetime, timedelta, timezone

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from routing.services import geo, heatmap
from routing.services.heatmap import build_heatmap, choose_grid, corridor_points
from routing.services.weather import HourlySeries

DEPARTURE = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
MIDNIGHT = DEPARTURE.replace(hour=0, minute=0)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def line(lat, lon_from, lon_to, n=20):
    return [(lat, lon_from + (lon_to - lon_from) * i / (n - 1)) for i in range(n)]


def test_corridor_points_stay_within_half_width():
    route = line(40.0, -100.0, -99.0)
    points = corridor_points([route], cell_deg=0.1, half_width_mi=20)
    cum = geo.cumulative_miles(route)
    for point in points:
        nearest = min(geo.haversine_mi(point, geo.point_at(route, cum, m)) for m in range(0, int(cum[-1]) + 1))
        assert nearest <= 20.5
    # 20 miles is ~0.29 deg of latitude, so the outermost 0.1 deg rows are 40.2 and 39.8.
    lats = {lat for lat, _ in points}
    assert max(lats) == 40.2
    assert min(lats) == 39.8


def test_overlapping_routes_share_points():
    a = line(40.0, -100.0, -99.0)
    assert corridor_points([a, a], 0.1) == corridor_points([a], 0.1)


def test_grid_coarsens_for_long_routes():
    short_cell, short_points = choose_grid([line(40.0, -100.0, -99.5)])
    long_cell, long_points = choose_grid([line(40.0, -120.0, -80.0, n=200)])
    assert short_cell == 0.1
    assert long_cell > short_cell
    assert len(short_points) <= heatmap.MAX_POINTS
    assert len(long_points) <= heatmap.MAX_POINTS


class RampWeather:
    """Wind rises 1 mph per hour from midnight; forecast ends after 30 hours."""

    def series(self, points, start, end):
        wind = tuple(float(h) for h in range(31))
        zeros = (0.0,) * 31
        return {p: HourlySeries(MIDNIGHT, wind, wind, zeros, zeros) for p in points}


def test_build_heatmap_hourly_values_from_departure():
    grid = build_heatmap([line(40.0, -100.0, -99.8)], DEPARTURE, load_lb=0, weather_client=RampWeather())
    assert grid["hours"] == 48
    assert grid["start"] == DEPARTURE.isoformat()
    n = len(grid["points"])
    assert n > 0 and len(grid["risk"]) == len(grid["wind_mph"]) == n

    wind = grid["wind_mph"][0]
    assert len(wind) == 49
    assert wind[0] == 8.5  # 08:30, interpolated
    assert wind[17] == 25.5  # 25.5 mph -> Moderate
    assert grid["risk"][0][16] == 0 and grid["risk"][0][17] == 1
    # Hours past the end of the forecast are unknown.
    assert wind[22] is None and grid["risk"][0][48] is None


def test_heatmap_risk_applies_load_rules():
    class Windy:
        def series(self, points, start, end):
            wind = (47.0,) * 72
            zeros = (0.0,) * 72
            return {p: HourlySeries(MIDNIGHT, wind, wind, zeros, zeros) for p in points}

    route = [line(40.0, -100.0, -99.8)]
    assert build_heatmap(route, DEPARTURE, 10_000, Windy())["risk"][0][0] == 3
    assert build_heatmap(route, DEPARTURE, 35_000, Windy())["risk"][0][0] == 4


def test_heatmap_endpoint(monkeypatch):
    monkeypatch.setattr("routing.views.OpenMeteoClient", RampWeather)
    departure = datetime.now(timezone.utc) + timedelta(hours=1)
    body = {
        "routes": [[[40.0, -100.0, 0], [40.0, -99.8, 600]]],
        "departure": departure.isoformat(),
        "load_lb": 20_000,
    }
    response = APIClient().post("/api/heatmap/", body, format="json")
    assert response.status_code == 200, response.data
    data = response.json()
    assert data["cell_deg"] == 0.1
    assert len(data["risk"]) == len(data["points"])


@pytest.mark.parametrize(
    "routes",
    [[], [[[40, -100]]], [[[40, -100], [95, -100]]], [[["a", "b"], [40, -100]]], [[[40, -100], [41, -100]]] * 4],
)
def test_heatmap_endpoint_rejects_bad_routes(routes):
    body = {"routes": routes, "departure": datetime.now(timezone.utc).isoformat(), "load_lb": 0}
    assert APIClient().post("/api/heatmap/", body, format="json").status_code == 400
