from datetime import date, datetime, timedelta, timezone

import pytest
from django.core.cache import cache

from routing.services import geo
from routing.services.assessment import UNKNOWN, assess_routes
from routing.services.errors import ProviderError
from routing.services.routing import Route
from routing.services.sampling import checkpoint_miles, place_checkpoints
from routing.services.weather import HourlySeries, OpenMeteoClient

DEPARTURE = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def eastbound_route(miles, hours):
    """A straight route along the equator with constant speed."""
    n = 101
    degrees = miles / geo.haversine_mi((0, 0), (0, 1))
    points = [(0.0, degrees * i / (n - 1)) for i in range(n)]
    cum = geo.cumulative_miles(points)
    seconds = [hours * 3600 * c / cum[-1] for c in cum]
    return Route(points, cum, seconds)


# --- sampling ----------------------------------------------------------------


def test_checkpoint_miles_include_origin_and_destination():
    assert checkpoint_miles(60, 25) == [0, 25, 50, 60]
    assert checkpoint_miles(50, 25) == [0, 25, 50]
    assert checkpoint_miles(8, 10) == [0, 8]


def test_checkpoints_cover_route_exactly_once():
    route = eastbound_route(miles=137, hours=2.5)
    checkpoints = place_checkpoints(route, DEPARTURE, 25)
    assert [round(cp.mile) for cp in checkpoints] == [0, 25, 50, 75, 100, 125, 137]
    assert checkpoints[0].covers_from_mi == 0
    assert checkpoints[-1].covers_to_mi == pytest.approx(route.distance_miles)
    for a, b in zip(checkpoints, checkpoints[1:]):
        assert a.covers_to_mi == b.covers_from_mi
    assert sum(cp.covered_miles for cp in checkpoints) == pytest.approx(route.distance_miles)


def test_checkpoint_eta_follows_route_timeline():
    route = eastbound_route(miles=100, hours=2)
    checkpoints = place_checkpoints(route, DEPARTURE, 50)
    assert [cp.eta for cp in checkpoints] == [
        DEPARTURE,
        DEPARTURE + timedelta(hours=1),
        DEPARTURE + timedelta(hours=2),
    ]


# --- hourly series lookup ------------------------------------------------------


def make_series(wind, rain=None, snow=None, gust=None, start=MIDNIGHT):
    n = len(wind)
    return HourlySeries(
        start=start,
        wind=tuple(wind),
        gust=tuple(gust or wind),
        rain=tuple(rain or [0.0] * n),
        snow=tuple(snow or [0.0] * n),
    )


def test_wind_is_interpolated_between_hours():
    series = make_series(wind=[10, 20, 30])
    assert series.at(MIDNIGHT + timedelta(minutes=90)).wind_mph == pytest.approx(25)


def test_precipitation_uses_hour_ending_after_eta():
    # rain[1] is the total for 00:00-01:00, rain[2] for 01:00-02:00.
    series = make_series(wind=[0, 0, 0], rain=[0.0, 0.1, 0.6])
    assert series.at(MIDNIGHT + timedelta(minutes=20)).rain_in_hr == 0.1
    assert series.at(MIDNIGHT + timedelta(minutes=80)).rain_in_hr == 0.6
    assert series.at(MIDNIGHT + timedelta(hours=1)).rain_in_hr == 0.1


def test_lookup_outside_series_or_missing_value_is_none():
    series = make_series(wind=[10, None, 30])
    assert series.at(MIDNIGHT - timedelta(minutes=1)) is None
    assert series.at(MIDNIGHT + timedelta(hours=2, minutes=1)) is None
    assert series.at(MIDNIGHT + timedelta(minutes=30)) is None


# --- Open-Meteo client ---------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.ok = payload, status, status < 400

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, status=200):
        self.requests, self.status = [], status

    def get(self, url, params, timeout):
        self.requests.append(params)
        n = len(params["latitude"].split(","))
        location = {
            "hourly": {
                "time": ["2026-10-14T00:00", "2026-10-14T01:00"],
                "wind_speed_10m": [5, 6],
                "wind_gusts_10m": [9, 9],
                "rain": [0, 0],
                "snowfall": [0, 0],
            }
        }
        return FakeResponse(location if n == 1 else [location] * n, self.status)


def test_client_batches_dedupes_and_caches(monkeypatch):
    monkeypatch.setattr("routing.services.weather.BATCH_SIZE", 2)
    session = FakeSession()
    client = OpenMeteoClient(session=session)
    points = [(1.0, 1.0), (2.0, 2.0), (1.0, 1.0), (3.0, 3.0)]
    result = client.series(points, DEPARTURE, DEPARTURE, today=date(2026, 10, 13))
    assert set(result) == {(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)}
    assert len(session.requests) == 2  # 3 unique points in batches of 2
    assert session.requests[0]["start_date"] == "2026-10-14"

    client.series(points, DEPARTURE, DEPARTURE, today=date(2026, 10, 13))
    assert len(session.requests) == 2  # served from cache


def test_client_clamps_to_forecast_horizon():
    session = FakeSession()
    client = OpenMeteoClient(session=session)
    today = date(2026, 10, 1)
    client.series([(1.0, 1.0)], datetime(2026, 10, 15, tzinfo=timezone.utc),
                  datetime(2026, 10, 20, tzinfo=timezone.utc), today=today)
    assert session.requests[0]["end_date"] == "2026-10-16"
    assert client.series([(2.0, 2.0)], datetime(2026, 10, 17, tzinfo=timezone.utc),
                         datetime(2026, 10, 18, tzinfo=timezone.utc), today=today) == {}


@pytest.mark.parametrize("status, message", [(429, "rate limit"), (500, "(500)")])
def test_client_raises_provider_error_on_failure(status, message):
    client = OpenMeteoClient(session=FakeSession(status=status))
    with pytest.raises(ProviderError, match=message):
        client.series([(1.0, 1.0)], DEPARTURE, DEPARTURE, today=date(2026, 10, 13))


def test_client_fetches_at_least_48_hours():
    session = FakeSession()
    OpenMeteoClient(session=session).series([(1.0, 1.0)], DEPARTURE, DEPARTURE, today=date(2026, 10, 13))
    assert session.requests[0]["end_date"] == "2026-10-16"


# --- assess_routes ---------------------------------------------------------------


class FakeWeather:
    """Wind speed grows with longitude so risk rises along an eastbound route."""

    def __init__(self, unknown_beyond_lon=None):
        self.unknown_beyond_lon = unknown_beyond_lon

    def series(self, points, start, end):
        result = {}
        for lat, lon in points:
            if self.unknown_beyond_lon is not None and lon > self.unknown_beyond_lon:
                continue
            wind = 20 if lon < 0.5 else 50  # Low, then Severe
            result[(lat, lon)] = make_series(wind=[wind] * 48)
        return result


def test_assess_routes_totals_and_average():
    route = eastbound_route(miles=100, hours=2)  # ends at ~1.45 deg longitude
    [assessment] = assess_routes([route], DEPARTURE, load_lb=10_000, interval_mi=10, weather_client=FakeWeather())
    miles = assessment.miles_by_level
    assert sum(miles.values()) == pytest.approx(100)
    assert miles["Low"] > 0 and miles["Severe"] > 0
    assert miles[UNKNOWN] == 0
    assert assessment.avg_risk == pytest.approx(3 * miles["Severe"] / 100)
    assert assessment.max_level.label == "Severe"


def test_heavy_load_turns_severe_wind_into_no_travel():
    route = eastbound_route(miles=100, hours=2)
    [assessment] = assess_routes([route], DEPARTURE, load_lb=35_000, interval_mi=10, weather_client=FakeWeather())
    assert assessment.miles_by_level["Severe"] == 0
    assert assessment.miles_by_level["No Travel"] > 0


def test_missing_forecast_counts_as_unknown_and_is_excluded_from_average():
    route = eastbound_route(miles=100, hours=2)
    [assessment] = assess_routes(
        [route], DEPARTURE, load_lb=0, interval_mi=10, weather_client=FakeWeather(unknown_beyond_lon=0.4)
    )
    assert assessment.miles_by_level[UNKNOWN] > 0
    assert assessment.avg_risk == 0  # only the Low stretch is known
    assert all(r.risk is None for r in assessment.checkpoints if r.level_label == UNKNOWN)
