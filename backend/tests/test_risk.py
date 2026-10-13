import pytest

from routing.services.risk import RAIN_BANDS, SNOW_BANDS, WIND_BANDS, RiskLevel, classify

L, M, H, S, N = (
    RiskLevel.LOW,
    RiskLevel.MODERATE,
    RiskLevel.HIGH,
    RiskLevel.SEVERE,
    RiskLevel.NO_TRAVEL,
)


@pytest.mark.parametrize(
    "mph, expected",
    [
        (0, L), (24.9, L),
        (25, M), (34.9, M),
        (35, H), (44.9, H),
        (45, S), (54.9, S),
        (55, N), (80, N),
    ],
)
def test_wind_bands(mph, expected):
    assert WIND_BANDS.classify(mph) == expected


@pytest.mark.parametrize(
    "in_hr, expected",
    [
        (0, L), (0.09, L),
        (0.10, M), (0.24, M),
        (0.25, H), (0.49, H),
        (0.50, S), (1.00, S),
        (1.01, N),
    ],
)
def test_rain_bands(in_hr, expected):
    assert RAIN_BANDS.classify(in_hr) == expected


@pytest.mark.parametrize(
    "in_hr, expected",
    [
        (0, L), (0.49, L),
        (0.5, M), (0.99, M),
        (1.0, H), (1.99, H),
        (2.0, S), (3.0, S),
        (3.01, N),
    ],
)
def test_snow_bands(in_hr, expected):
    assert SNOW_BANDS.classify(in_hr) == expected


def test_worst_condition_wins():
    result = classify(wind_mph=10, rain_in_hr=0.3, snow_in_hr=0.6, load_lb=10_000)
    assert result.level == H
    assert (result.wind_level, result.rain_level, result.snow_level) == (L, H, M)


def test_calm_weather_is_low_with_no_reasons():
    result = classify(0, 0, 0, 80_000)
    assert result.level == L
    assert result.reasons == []


@pytest.mark.parametrize("load", [0, 30_000, 80_000])
def test_wind_55_is_no_travel_for_any_load(load):
    assert classify(55, 0, 0, load).level == N


@pytest.mark.parametrize(
    "wind, load, expected",
    [
        (45, 30_000, S),  # at the limit, not above it
        (45, 30_001, N),
        (54.9, 30_001, N),
        (44.9, 30_001, H),  # wind band below the rule
    ],
)
def test_load_rule_45_54_mph(wind, load, expected):
    assert classify(wind, 0, 0, load).level == expected


@pytest.mark.parametrize(
    "wind, load, expected",
    [
        (35, 40_000, H),
        (35, 40_001, S),
        (44.9, 40_001, S),
        (34.9, 40_001, M),
    ],
)
def test_load_rule_35_44_mph(wind, load, expected):
    assert classify(wind, 0, 0, load).level == expected


def test_load_rule_never_lowers_level():
    # Rain alone is No Travel; the 35-44 mph load rule must not pull it down to Severe.
    assert classify(40, 1.5, 0, 45_000).level == N


def test_load_rule_reason_is_reported():
    result = classify(47, 0, 0, 35_000)
    assert result.level == N
    assert any("Load 35,000 lb" in r for r in result.reasons)


@pytest.mark.parametrize("args", [(-1, 0, 0, 0), (0, -0.1, 0, 0), (0, 0, -1, 0), (0, 0, 0, -1)])
def test_negative_inputs_rejected(args):
    with pytest.raises(ValueError):
        classify(*args)


def test_to_dict_is_json_friendly():
    data = classify(30, 0, 0, 0).to_dict()
    assert data["level"] == "Moderate"
    assert data["score"] == 1
    assert data["wind_level"] == "Moderate"
