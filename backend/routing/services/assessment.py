"""Weather risk along each route: checkpoint conditions, risk levels and mile totals."""

from dataclasses import dataclass
from datetime import timedelta

from .risk import RiskLevel, classify
from .sampling import place_checkpoints
from .weather import cell_of

UNKNOWN = "Unknown"
LEVEL_LABELS = [level.label for level in RiskLevel] + [UNKNOWN]


@dataclass
class CheckpointResult:
    checkpoint: object  # sampling.Checkpoint
    conditions: object  # weather.Conditions, or None when no forecast covers the ETA
    risk: object  # risk.RiskAssessment, or None when conditions are unknown

    @property
    def level_label(self):
        return self.risk.level.label if self.risk else UNKNOWN


@dataclass
class RouteAssessment:
    route: object  # routing.Route
    checkpoints: list  # [CheckpointResult]
    miles_by_level: dict  # label -> miles, including "Unknown"
    avg_risk: object  # distance-weighted mean score 0-4 over known miles, or None
    max_level: object  # worst RiskLevel seen, or None if nothing is known

    def miles(self, level):
        return self.miles_by_level[level.label]


def summarize(route, results):
    miles_by_level = dict.fromkeys(LEVEL_LABELS, 0.0)
    weighted, known_miles = 0.0, 0.0
    for result in results:
        covered = result.checkpoint.covered_miles
        miles_by_level[result.level_label] += covered
        if result.risk:
            weighted += int(result.risk.level) * covered
            known_miles += covered
    known_levels = [r.risk.level for r in results if r.risk]
    return RouteAssessment(
        route=route,
        checkpoints=results,
        miles_by_level=miles_by_level,
        avg_risk=weighted / known_miles if known_miles else None,
        max_level=max(known_levels) if known_levels else None,
    )


def assess_routes(routes, departure, load_lb, interval_mi, weather_client):
    """Checkpoints, forecast-at-ETA and risk for every route, in one batched weather fetch."""
    checkpoints_by_route = [place_checkpoints(route, departure, interval_mi) for route in routes]
    cells = [cell_of(cp.lat, cp.lon) for cps in checkpoints_by_route for cp in cps]
    latest_arrival = departure + timedelta(seconds=max(route.duration_s for route in routes))
    series_by_cell = weather_client.series(cells, departure, latest_arrival)

    assessments = []
    for route, checkpoints in zip(routes, checkpoints_by_route):
        results = []
        for cp in checkpoints:
            series = series_by_cell.get(cell_of(cp.lat, cp.lon))
            conditions = series.at(cp.eta) if series else None
            risk = (
                classify(conditions.wind_mph, conditions.rain_in_hr, conditions.snow_in_hr, load_lb)
                if conditions
                else None
            )
            results.append(CheckpointResult(cp, conditions, risk))
        assessments.append(summarize(route, results))
    return assessments
