from datetime import datetime, timezone

import flexpolyline
import pytest

from routing.services import geo
from routing.services.errors import ProviderError
from routing.services.routing import (
    Route,
    corridor_cells,
    overlap,
    plan_routes,
    route_from_here,
    section_timeline,
)

DEPARTURE = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)


def straight_line(start, end, n=200):
    return [
        (start[0] + (end[0] - start[0]) * i / (n - 1), start[1] + (end[1] - start[1]) * i / (n - 1))
        for i in range(n)
    ]


def raw_route(points, duration_s, spans=None):
    return {
        "sections": [
            {
                "polyline": flexpolyline.encode(points),
                "summary": {"duration": duration_s},
                "spans": spans,
            }
        ]
    }


# --- geo -------------------------------------------------------------------


def test_haversine_one_degree_latitude():
    assert geo.haversine_mi((0, 0), (1, 0)) == pytest.approx(69.09, abs=0.05)


def test_point_and_value_interpolation():
    points = [(0, 0), (1, 0), (2, 0)]
    cum = geo.cumulative_miles(points)
    lat, lon = geo.point_at(points, cum, cum[-1] * 0.75)
    assert (lat, lon) == pytest.approx((1.5, 0))
    assert geo.value_at(cum, [0, 100, 300], cum[1] / 2) == pytest.approx(50)
    # Clamped at both ends.
    assert geo.point_at(points, cum, -5) == (0, 0)
    assert geo.point_at(points, cum, cum[-1] + 5) == (2, 0)


def test_offset_point_goes_left_and_right():
    origin, dest = (30.0, -100.0), (30.0, -90.0)  # heading east
    left = geo.offset_point(origin, dest, 0.5, 50)
    right = geo.offset_point(origin, dest, 0.5, -50)
    assert left[0] > 30 > right[0]  # left of eastbound is north
    assert left[1] == pytest.approx(-95.0, abs=1e-6)
    assert geo.haversine_mi((30.0, -95.0), left) == pytest.approx(50, rel=0.01)


# --- timeline --------------------------------------------------------------


def test_section_timeline_uses_per_span_speeds():
    points = [(0, 0), (0.1, 0), (0.2, 0), (0.3, 0), (0.4, 0)]
    # First half slow (300 s), second half fast (100 s).
    times = section_timeline(points, [{"offset": 0, "duration": 300}, {"offset": 2, "duration": 100}], 400)
    assert times == pytest.approx([0, 150, 300, 350, 400])


def test_section_timeline_without_spans_is_proportional():
    points = [(0, 0), (0.1, 0), (0.3, 0)]
    assert section_timeline(points, None, 300) == pytest.approx([0, 100, 300])


def test_route_from_here_joins_sections_without_duplicate_point():
    a = [(0, 0), (0.1, 0), (0.2, 0)]
    b = [(0.2, 0), (0.3, 0)]
    raw = {
        "sections": [
            {"polyline": flexpolyline.encode(a), "summary": {"duration": 200}, "spans": None},
            {"polyline": flexpolyline.encode(b), "summary": {"duration": 50}, "spans": None},
        ]
    }
    route = route_from_here(raw)
    assert len(route.points) == 4
    assert route.cum_seconds == pytest.approx([0, 100, 200, 250])
    assert route.duration_s == pytest.approx(250)


# --- dedupe ----------------------------------------------------------------


def make_route(points, duration_s=3600, source="provider"):
    times = [duration_s * i / (len(points) - 1) for i in range(len(points))]
    return Route(points, geo.cumulative_miles(points), times, source)


def test_overlap_identical_and_disjoint():
    a = make_route(straight_line((30, -100), (31, -99)))
    b = make_route(straight_line((40, -100), (41, -99)))
    assert overlap(corridor_cells(a), corridor_cells(a)) == 1
    assert overlap(corridor_cells(a), corridor_cells(b)) == 0


# --- plan_routes -------------------------------------------------------------

ORIGIN, DEST = (30.0, -100.0), (30.0, -96.0)


class FakeClient:
    def __init__(self, provider_routes, detour_fails=False):
        self.provider_routes = provider_routes
        self.detour_fails = detour_fails
        self.calls = []

    def truck_routes(self, origin, destination, departure, gross_weight_kg, alternatives=2, via=None):
        self.calls.append({"via": via, "gross_weight_kg": gross_weight_kg})
        if via is None:
            return self.provider_routes
        if self.detour_fails:
            raise ProviderError("no route")
        return [raw_route(straight_line(origin, via) + straight_line(via, destination)[1:], 5000)]


def test_three_provider_routes_need_no_detours():
    routes = [
        raw_route(straight_line(ORIGIN, DEST), 4000),
        raw_route(straight_line(ORIGIN, (31, -98)) + straight_line((31, -98), DEST)[1:], 4500),
        raw_route(straight_line(ORIGIN, (29, -98)) + straight_line((29, -98), DEST)[1:], 4600),
    ]
    client = FakeClient(routes)
    result = plan_routes(client, ORIGIN, DEST, DEPARTURE, load_lb=20_000)
    assert len(result) == 3
    assert len(client.calls) == 1
    assert [r.duration_s for r in result] == sorted(r.duration_s for r in result)


def test_gross_weight_includes_truck_tare():
    client = FakeClient([raw_route(straight_line(ORIGIN, DEST), 4000)] * 3)
    plan_routes(client, ORIGIN, DEST, DEPARTURE, load_lb=45_000)
    assert client.calls[0]["gross_weight_kg"] == pytest.approx(80_000 / 2.20462)


def test_duplicate_provider_routes_are_filled_with_detours():
    same = raw_route(straight_line(ORIGIN, DEST), 4000)
    client = FakeClient([same, same])
    result = plan_routes(client, ORIGIN, DEST, DEPARTURE, load_lb=0)
    assert len(result) == 3
    assert [r.source for r in result].count("detour") == 2
    assert all(call["via"] for call in client.calls[1:])


def test_failed_detours_return_fewer_routes():
    client = FakeClient([raw_route(straight_line(ORIGIN, DEST), 4000)], detour_fails=True)
    result = plan_routes(client, ORIGIN, DEST, DEPARTURE, load_lb=0)
    assert len(result) == 1


def test_detour_much_slower_than_fastest_is_dropped():
    client = FakeClient([raw_route(straight_line(ORIGIN, DEST), 1000)])  # detours take 5000 s
    result = plan_routes(client, ORIGIN, DEST, DEPARTURE, load_lb=0)
    assert len(result) == 1
