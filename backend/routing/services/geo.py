"""Geometry helpers for (lat, lon) polylines. Distances are in miles."""

import math
from bisect import bisect_left

EARTH_RADIUS_MI = 3958.7613
MILES_PER_DEG_LAT = 69.055


def haversine_mi(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(h))


def cumulative_miles(points):
    """Distance from the first point to each point along the polyline."""
    cum = [0.0]
    for prev, cur in zip(points, points[1:]):
        cum.append(cum[-1] + haversine_mi(prev, cur))
    return cum


def locate(cum, target):
    """Return (i, frac) such that target lies frac of the way from point i to point i+1."""
    if target <= cum[0]:
        return 0, 0.0
    if target >= cum[-1]:
        return len(cum) - 2, 1.0
    j = bisect_left(cum, target)
    i = j - 1
    span = cum[j] - cum[i]
    return i, (target - cum[i]) / span if span else 0.0


def point_at(points, cum, target_mi):
    """The (lat, lon) located target_mi miles along the polyline."""
    i, frac = locate(cum, target_mi)
    (lat1, lon1), (lat2, lon2) = points[i], points[i + 1]
    return (lat1 + (lat2 - lat1) * frac, lon1 + (lon2 - lon1) * frac)


def value_at(cum, values, target_mi):
    """Linearly interpolate a per-point series (e.g. elapsed seconds) at target_mi."""
    i, frac = locate(cum, target_mi)
    return values[i] + (values[i + 1] - values[i]) * frac


def offset_point(point, toward, ratio_along, offset_mi):
    """A point ratio_along of the way from point to toward, shifted offset_mi to the left
    of that direction (negative offset_mi shifts right). Flat-earth approximation,
    fine for picking detour waypoints."""
    lat0, lon0 = point
    lat1, lon1 = toward
    cos_lat = math.cos(math.radians((lat0 + lat1) / 2))
    # Work in a local frame measured in miles.
    dx = (lon1 - lon0) * MILES_PER_DEG_LAT * cos_lat
    dy = (lat1 - lat0) * MILES_PER_DEG_LAT
    length = math.hypot(dx, dy) or 1.0
    px, py = -dy / length, dx / length  # unit vector pointing left
    x = dx * ratio_along + px * offset_mi
    y = dy * ratio_along + py * offset_mi
    return (lat0 + y / MILES_PER_DEG_LAT, lon0 + x / (MILES_PER_DEG_LAT * cos_lat))


def simplify(points, tolerance_deg):
    """Indices of the points kept by Ramer-Douglas-Peucker simplification.

    Returns indices rather than points so callers can keep per-point data
    (such as elapsed time) aligned with the simplified line.
    """
    n = len(points)
    if n < 3:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        (y1, x1), (y2, x2) = points[start], points[end]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        max_dist, max_i = 0.0, None
        for i in range(start + 1, end):
            y, x = points[i]
            if norm:
                dist = abs(dy * (x - x1) - dx * (y - y1)) / norm
            else:
                dist = math.hypot(x - x1, y - y1)
            if dist > max_dist:
                max_dist, max_i = dist, i
        if max_i is not None and max_dist > tolerance_deg:
            keep[max_i] = True
            stack.append((start, max_i))
            stack.append((max_i, end))
    return [i for i in range(n) if keep[i]]
