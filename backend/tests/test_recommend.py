from types import SimpleNamespace

from routing.services.assessment import LEVEL_LABELS, RouteAssessment
from routing.services.recommend import rank, recommend
from routing.services.risk import RiskLevel


def assessment(name, hours, avg=0.0, unknown=0, **miles):
    """miles keys: low, moderate, high, severe, no_travel."""
    by_level = dict.fromkeys(LEVEL_LABELS, 0.0)
    for key, value in miles.items():
        by_level[RiskLevel[key.upper()].label] = value
    by_level["Unknown"] = unknown
    levels = [RiskLevel[k.upper()] for k, v in miles.items() if v]
    return RouteAssessment(
        route=SimpleNamespace(name=name, duration_s=hours * 3600),
        checkpoints=[],
        miles_by_level=by_level,
        avg_risk=avg,
        max_level=max(levels) if levels else RiskLevel.LOW,
    )


def names(ranked):
    return [a.route.name for a in ranked]


def test_fewest_severe_miles_beats_faster_route():
    fast = assessment("A", 5, severe=40, low=260)
    slow = assessment("B", 7, high=30, low=290)
    assert names(rank([fast, slow])) == ["B", "A"]


def test_no_travel_ranks_below_severe():
    no_travel = assessment("A", 5, no_travel=5, low=295)
    severe = assessment("B", 6, severe=50, low=250)
    assert names(rank([no_travel, severe])) == ["B", "A"]


def test_high_miles_break_severe_tie():
    a = assessment("A", 5, severe=10, high=50)
    b = assessment("B", 6, severe=10, high=20)
    assert names(rank([a, b])) == ["B", "A"]


def test_average_risk_breaks_high_tie():
    a = assessment("A", 5, moderate=200, avg=0.8)
    b = assessment("B", 6, moderate=20, avg=0.1)
    assert names(rank([a, b])) == ["B", "A"]


def test_travel_time_decides_when_risk_is_equal():
    a = assessment("A", 6, low=300)
    b = assessment("B", 5, low=300)
    assert names(rank([a, b])) == ["B", "A"]


def test_differences_within_tolerance_are_ties():
    # 0.5 Severe miles and 0.03 average risk are within tolerance: the faster route wins.
    a = assessment("A", 5, severe=0.5, low=299.5, avg=0.03)
    b = assessment("B", 7, low=300, avg=0.0)
    assert names(rank([a, b])) == ["A", "B"]


def test_full_ranking_of_three():
    a = assessment("A", 5, severe=30)
    b = assessment("B", 6, high=30, avg=0.5)
    c = assessment("C", 7, high=30, avg=0.2)
    assert names(rank([a, b, c])) == ["C", "B", "A"]


def test_recommendation_explains_and_warns():
    a = assessment("A", 5, no_travel=10, low=290)
    b = assessment("B", 5.5, no_travel=20, low=280)
    result = recommend([a, b], lambda x: x.route.name)
    assert names(result.ranked) == ["A", "B"]
    assert result.headline.startswith("Route A is recommended")
    assert result.reasons == ["vs Route B: 10 No Travel miles instead of 20 No Travel miles; 30 min faster."]
    assert "Consider delaying departure" in result.warning


def test_recommendation_reports_extra_time_and_unknown_miles():
    a = assessment("A", 5, severe=40, low=260)
    b = assessment("B", 6.25, low=280, unknown=20)
    result = recommend([a, b], lambda x: x.route.name)
    assert result.reasons == ["vs Route A: 0 Severe miles instead of 40 Severe miles; 1 h 15 min longer."]
    assert "20 miles are beyond the forecast range" in result.warning
