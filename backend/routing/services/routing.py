"""Turn routing-provider responses into Route objects with per-point distance and time.

Each Route carries, for every polyline point, the miles and seconds elapsed since
departure. Checkpoint placement and ETAs are then simple interpolations.
"""

from dataclasses import dataclass

import flexpolyline

from . import geo
from .here import ProviderError

LB_PER_KG = 2.20462
# Typical empty tractor-trailer weight; HERE wants gross (truck + load) weight.
TRUCK_TARE_LB = 35_000

TARGET_ROUTES = 3
# Two routes sharing at least this fraction of their corridor count as the same route.
DUPLICATE_OVERLAP = 0.85
# Detours slower than this multiple of the fastest route are not worth offering.
MAX_DETOUR_DURATION_RATIO = 1.6
# Detour waypoints: (fraction along the trip, sideways offset as a fraction of trip length).
DETOUR_WAYPOINTS = [(0.5, 0.15), (0.5, -0.15), (0.35, 0.25), (0.65, -0.25)]
DETOUR_MIN_OFFSET_MI = 10
DETOUR_MAX_OFFSET_MI = 150


@dataclass
class Route:
    points: list  # [(lat, lon), ...]
    cum_miles: list  # miles from origin at each point
    cum_seconds: list  # seconds from departure at each point
    source: str = "provider"  # "provider" or "detour"

    @property
    def distance_miles(self):
        return self.cum_miles[-1]

    @property
    def duration_s(self):
        return self.cum_seconds[-1]


def section_timeline(points, spans, duration_s):
    """Seconds from section start at each point.

    Each span covers points[offset .. next offset] and has a duration; that time
    is spread over the span's points in proportion to distance. Without spans,
    the whole section is treated as one span.
    """
    spans = sorted(spans or [{"offset": 0, "duration": duration_s}], key=lambda s: s["offset"])
    times = [0.0] * len(points)
    for k, span in enumerate(spans):
        start = span["offset"]
        end = spans[k + 1]["offset"] if k + 1 < len(spans) else len(points) - 1
        legs = [geo.haversine_mi(points[i], points[i + 1]) for i in range(start, end)]
        total = sum(legs)
        for step, i in enumerate(range(start, end)):
            share = legs[step] / total if total else 1 / len(legs)
            times[i + 1] = times[i] + span["duration"] * share
    return times


def route_from_here(raw, source="provider"):
    points, seconds = [], []
    for section in raw["sections"]:
        pts = [(p[0], p[1]) for p in flexpolyline.decode(section["polyline"])]
        times = section_timeline(pts, section.get("spans"), section["summary"]["duration"])
        offset = seconds[-1] if seconds else 0.0
        if points and points[-1] == pts[0]:
            pts, times = pts[1:], times[1:]
        points.extend(pts)
        seconds.extend(offset + t for t in times)
    return Route(points, geo.cumulative_miles(points), seconds, source)


def corridor_cells(route, step_mi=0.5, cell_deg=0.01):
    """Grid cells (~0.7 mi) the route passes through, sampled every step_mi."""
    cells = set()
    mile = 0.0
    while mile <= route.distance_miles:
        lat, lon = geo.point_at(route.points, route.cum_miles, mile)
        cells.add((round(lat / cell_deg), round(lon / cell_deg)))
        mile += step_mi
    return cells


def overlap(a_cells, b_cells):
    return len(a_cells & b_cells) / min(len(a_cells), len(b_cells))


def add_if_distinct(route, accepted):
    """Append route unless it duplicates one already accepted. Returns True if added."""
    cells = corridor_cells(route)
    if any(overlap(cells, other_cells) >= DUPLICATE_OVERLAP for _, other_cells in accepted):
        return False
    accepted.append((route, cells))
    return True


def plan_routes(client, origin, destination, departure, load_lb):
    """Up to TARGET_ROUTES distinct truck routes, fastest first.

    Uses the provider's own alternatives, then fills any gap with detours forced
    through waypoints off to either side of the trip.
    """
    gross_kg = (TRUCK_TARE_LB + load_lb) / LB_PER_KG
    accepted = []
    for raw in client.truck_routes(origin, destination, departure, gross_kg, alternatives=TARGET_ROUTES - 1):
        add_if_distinct(route_from_here(raw), accepted)
        if len(accepted) == TARGET_ROUTES:
            break

    fastest_s = min(route.duration_s for route, _ in accepted)
    trip_mi = geo.haversine_mi(origin, destination)
    for ratio, offset_share in DETOUR_WAYPOINTS:
        if len(accepted) >= TARGET_ROUTES:
            break
        offset_mi = max(DETOUR_MIN_OFFSET_MI, min(DETOUR_MAX_OFFSET_MI, trip_mi * abs(offset_share)))
        via = geo.offset_point(origin, destination, ratio, offset_mi if offset_share > 0 else -offset_mi)
        try:
            raw = client.truck_routes(origin, destination, departure, gross_kg, alternatives=0, via=via)[0]
        except ProviderError:
            continue  # e.g. waypoint in a lake or off the road network
        detour = route_from_here(raw, source="detour")
        if detour.duration_s <= fastest_s * MAX_DETOUR_DURATION_RATIO:
            add_if_distinct(detour, accepted)

    return sorted((route for route, _ in accepted), key=lambda r: r.duration_s)
