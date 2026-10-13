from datetime import datetime, timedelta, timezone

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from routing.services import geo
from routing.services.errors import ProviderError
from routing.services.routing import Route
from routing.services.weather import HourlySeries


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def line_route(lat, hours, n=50):
    points = [(lat, -100 + 2 * i / (n - 1)) for i in range(n)]
    cum = geo.cumulative_miles(points)
    return Route(points, cum, [hours * 3600 * c / cum[-1] for c in cum])


class FakeHere:
    def geocode(self, query, limit=5):
        if query == "Nowhere":
            return []
        return [{"label": query, "lat": 35.0, "lon": -100.0}][:limit]


class FakeWeather:
    def __init__(self, wind_by_lat):
        self.wind_by_lat = wind_by_lat

    def series(self, cells, start, end):
        midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
        result = {}
        for cell in cells:
            wind = self.wind_by_lat.get(round(cell[0] / 10), 0)
            result[cell] = HourlySeries(midnight, (wind,) * 72, (wind,) * 72, (0.0,) * 72, (0.0,) * 72)
        return result


@pytest.fixture
def fakes(monkeypatch):
    routes = [line_route(35, 5), line_route(36, 6), line_route(37, 7)]
    monkeypatch.setattr("routing.views.HereClient", FakeHere)
    monkeypatch.setattr("routing.views.plan_routes", lambda *args: routes)
    # Fastest route (lat 35) is windy enough to be Severe; the others are calm.
    monkeypatch.setattr("routing.views.OpenMeteoClient", lambda: FakeWeather({35: 47, 36: 10, 37: 10}))
    return routes


def plan_body(**overrides):
    body = {
        "origin": "Amarillo, TX",
        "destination": {"lat": 35.0, "lon": -98.0, "label": "Somewhere"},
        "departure": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "load_lb": 20_000,
        "interval_mi": 25,
    }
    return {**body, **overrides}


def test_plan_returns_routes_and_recommendation(fakes):
    response = APIClient().post("/api/plan/", plan_body(), format="json")
    assert response.status_code == 200, response.data
    data = response.json()
    assert [r["id"] for r in data["routes"]] == ["A", "B", "C"]
    # A is fastest but Severe; B is the fastest calm route.
    assert data["recommendation"]["route_id"] == "B"
    assert data["recommendation"]["ranking"] == ["B", "C", "A"]
    route_a = data["routes"][0]
    assert route_a["summary"]["max_level"] == "Severe"
    assert route_a["checkpoints"][0]["level"] == "Severe"
    assert route_a["checkpoints"][0]["conditions"]["wind_mph"] == 47
    assert len(route_a["geometry"][0]) == 3  # lat, lon, seconds
    assert data["origin"]["label"] == "Amarillo, TX"


def test_heavy_load_upgrades_to_no_travel(fakes):
    response = APIClient().post("/api/plan/", plan_body(load_lb=35_000), format="json")
    route_a = response.json()["routes"][0]
    assert route_a["summary"]["max_level"] == "No Travel"


@pytest.mark.parametrize(
    "override",
    [
        {"load_lb": -1},
        {"load_lb": 90_000},
        {"interval_mi": 15},
        {"departure": "2020-01-01T00:00:00Z"},
        {"origin": {"lat": 200, "lon": 0}},
        {"origin": ""},
    ],
)
def test_plan_rejects_invalid_input(fakes, override):
    response = APIClient().post("/api/plan/", plan_body(**override), format="json")
    assert response.status_code == 400


def test_unknown_place_is_a_502_with_message(fakes):
    response = APIClient().post("/api/plan/", plan_body(origin="Nowhere"), format="json")
    assert response.status_code == 502
    assert "Nowhere" in response.json()["detail"]


def test_provider_failure_is_reported(monkeypatch, fakes):
    def fail(*args):
        raise ProviderError("No truck route found.")

    monkeypatch.setattr("routing.views.plan_routes", fail)
    response = APIClient().post("/api/plan/", plan_body(), format="json")
    assert response.status_code == 502
    assert response.json()["detail"] == "No truck route found."


def test_geocode_short_query_returns_empty(fakes):
    assert APIClient().get("/api/geocode/", {"q": "ab"}).json() == []
    assert APIClient().get("/api/geocode/", {"q": "Dallas"}).json()[0]["label"] == "Dallas"
