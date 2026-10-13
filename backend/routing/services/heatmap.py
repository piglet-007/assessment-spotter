"""Hourly weather grid over the trip corridor, for the 0-48 hour forecast slider.

The grid covers every point within CORRIDOR_HALF_WIDTH_MI of any route. Cell
size starts at the weather grid's 0.1 degrees and coarsens until the grid
fits in MAX_POINTS, so short trips get fine detail and long trips stay within
the weather API's request budget.
"""

import math
from datetime import timedelta

from . import geo
from .risk import classify
from .weather import CELL_DEG, snap

HEATMAP_HOURS = 48
CORRIDOR_HALF_WIDTH_MI = 30
MAX_POINTS = 200  # Open-Meteo's free tier allows ~600 locations per minute
CELL_SIZES_DEG = (CELL_DEG, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0)


def corridor_points(polylines, cell_deg, half_width_mi=CORRIDOR_HALF_WIDTH_MI):
    """Snapped grid points within half_width_mi of any polyline."""
    cell_mi = cell_deg * geo.MILES_PER_DEG_LAT
    step_mi = max(cell_mi / 2, 1.0)
    found = set()
    for points in polylines:
        cum = geo.cumulative_miles(points)
        mile = 0.0
        while True:
            lat, lon = geo.point_at(points, cum, min(mile, cum[-1]))
            lat_span = half_width_mi / geo.MILES_PER_DEG_LAT
            lon_span = lat_span / max(math.cos(math.radians(lat)), 0.1)
            for i in range(math.floor((lat - lat_span) / cell_deg), math.ceil((lat + lat_span) / cell_deg) + 1):
                for j in range(math.floor((lon - lon_span) / cell_deg), math.ceil((lon + lon_span) / cell_deg) + 1):
                    candidate = snap(i * cell_deg, j * cell_deg, cell_deg)
                    if geo.haversine_mi((lat, lon), candidate) <= half_width_mi:
                        found.add(candidate)
            if mile >= cum[-1]:
                break
            mile += step_mi
    return sorted(found)


def choose_grid(polylines):
    """The finest cell size whose corridor grid fits in MAX_POINTS."""
    for cell_deg in CELL_SIZES_DEG:
        points = corridor_points(polylines, cell_deg)
        if len(points) <= MAX_POINTS:
            break
    return cell_deg, points


def build_heatmap(polylines, departure, load_lb, weather_client):
    """Per grid point, hourly risk score and raw conditions from departure to +48 h.

    Values are None where no forecast covers that hour (beyond the horizon).
    """
    cell_deg, points = choose_grid(polylines)
    end = departure + timedelta(hours=HEATMAP_HOURS)
    series_by_point = weather_client.series(points, departure, end)
    times = [departure + timedelta(hours=h) for h in range(HEATMAP_HOURS + 1)]

    risk, wind, rain, snow = [], [], [], []
    for point in points:
        series = series_by_point.get(point)
        row = [series.at(t) if series else None for t in times]
        risk.append([int(classify(c.wind_mph, c.rain_in_hr, c.snow_in_hr, load_lb).level) if c else None for c in row])
        wind.append([round(c.wind_mph, 1) if c else None for c in row])
        rain.append([round(c.rain_in_hr, 3) if c else None for c in row])
        snow.append([round(c.snow_in_hr, 3) if c else None for c in row])

    return {
        "start": departure.isoformat(),
        "hours": HEATMAP_HOURS,
        "cell_deg": cell_deg,
        "points": [[lat, lon] for lat, lon in points],
        "risk": risk,
        "wind_mph": wind,
        "rain_in_hr": rain,
        "snow_in_hr": snow,
    }
