"""Rank assessed routes and explain the pick.

Order of preference, per the spec: fewest Severe miles, fewest High miles,
lowest average risk, shortest travel time. No Travel miles are ranked ahead
of all of these, since they are strictly worse than Severe.

Each risk criterion has a small tolerance: two routes within it are tied on
that criterion and the next one decides. Without this, sampling noise (a
fraction of a mile of Severe weather) could outweigh hours of extra driving.
"""

from dataclasses import dataclass

from .assessment import UNKNOWN
from .risk import RiskLevel

MILES_TOLERANCE = 1.0
AVG_RISK_TOLERANCE = 0.05


@dataclass(frozen=True)
class Criterion:
    name: str
    tolerance: float
    value: object  # RouteAssessment -> float, lower is better
    describe: object  # RouteAssessment -> str


def _miles(level):
    return lambda a: a.miles(level)


def _describe_miles(level):
    return lambda a: f"{a.miles(level):.0f} {level.label} miles"


def _avg_risk(a):
    # A route with no known weather at all cannot be judged on average risk.
    return a.avg_risk if a.avg_risk is not None else float(RiskLevel.NO_TRAVEL)


CRITERIA = [
    Criterion("no_travel_miles", MILES_TOLERANCE, _miles(RiskLevel.NO_TRAVEL), _describe_miles(RiskLevel.NO_TRAVEL)),
    Criterion("severe_miles", MILES_TOLERANCE, _miles(RiskLevel.SEVERE), _describe_miles(RiskLevel.SEVERE)),
    Criterion("high_miles", MILES_TOLERANCE, _miles(RiskLevel.HIGH), _describe_miles(RiskLevel.HIGH)),
    Criterion("avg_risk", AVG_RISK_TOLERANCE, _avg_risk, lambda a: f"average risk {_avg_risk(a):.2f}"),
]


def pick_best(candidates):
    """Keep the routes tied with the best on each criterion in turn, then take the fastest."""
    for criterion in CRITERIA:
        best = min(criterion.value(a) for a in candidates)
        candidates = [a for a in candidates if criterion.value(a) - best <= criterion.tolerance]
    return min(candidates, key=lambda a: a.route.duration_s)


def rank(assessments):
    remaining, ranked = list(assessments), []
    while remaining:
        best = pick_best(remaining)
        ranked.append(best)
        remaining.remove(best)
    return ranked


def format_duration(seconds):
    minutes = round(abs(seconds) / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def compare(best, other, name_of):
    """One sentence on why best beat other: the first criterion that separated them."""
    extra_time = best.route.duration_s - other.route.duration_s
    if extra_time > 60:
        time_note = f"{format_duration(extra_time)} longer"
    elif extra_time < -60:
        time_note = f"{format_duration(extra_time)} faster"
    else:
        time_note = "about the same travel time"

    for criterion in CRITERIA:
        if criterion.value(other) - criterion.value(best) > criterion.tolerance:
            return (
                f"vs Route {name_of(other)}: {criterion.describe(best)} instead of "
                f"{criterion.describe(other)}; {time_note}."
            )
    return f"vs Route {name_of(other)}: similar weather risk; {time_note}."


@dataclass
class Recommendation:
    ranked: list  # [RouteAssessment], best first
    headline: str
    reasons: list
    warning: object  # str or None


def recommend(assessments, name_of):
    """Rank routes and build the explanation. name_of maps an assessment to its label (A, B, C)."""
    ranked = rank(assessments)
    best = ranked[0]
    worst = best.max_level.label if best.max_level is not None else UNKNOWN
    headline = (
        f"Route {name_of(best)} is recommended: worst conditions {worst}, "
        f"{format_duration(best.route.duration_s)} drive."
    )
    reasons = [compare(best, other, name_of) for other in ranked[1:]]

    warnings = []
    if best.miles(RiskLevel.NO_TRAVEL) > 0:
        warnings.append(
            "Every route crosses No Travel conditions at the planned time. Consider delaying departure."
        )
    elif best.miles(RiskLevel.SEVERE) > 0:
        warnings.append("The recommended route still has Severe weather. Drive with extra caution.")
    if best.miles_by_level[UNKNOWN] > 0:
        warnings.append(
            f"{best.miles_by_level[UNKNOWN]:.0f} miles are beyond the forecast range and could not be assessed."
        )

    return Recommendation(ranked, headline, reasons, " ".join(warnings) or None)
