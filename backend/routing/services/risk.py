"""Weather risk classification for a single checkpoint.

Units: wind in mph, rain and snow in inches per hour, load in pounds.

The spec's bands share their edges (e.g. rain "0.10-0.25" and "0.25-0.50"),
so each band includes its lower bound and excludes its upper bound. The top
band is the exception the spec spells out: wind is No Travel at >= 55 mph,
while rain and snow are No Travel only *above* 1.00 / 3.0 in/hr.
"""

from dataclasses import dataclass, field
from enum import IntEnum


class RiskLevel(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self):
        return self.name.replace("_", " ").title()


@dataclass(frozen=True)
class Bands:
    """Lower bounds of the Moderate, High and Severe bands, plus the No Travel cutoff."""

    moderate: float
    high: float
    severe: float
    no_travel: float
    no_travel_inclusive: bool

    def classify(self, value):
        if value < 0:
            raise ValueError(f"weather values cannot be negative, got {value}")
        if value > self.no_travel or (self.no_travel_inclusive and value == self.no_travel):
            return RiskLevel.NO_TRAVEL
        if value >= self.severe:
            return RiskLevel.SEVERE
        if value >= self.high:
            return RiskLevel.HIGH
        if value >= self.moderate:
            return RiskLevel.MODERATE
        return RiskLevel.LOW


WIND_BANDS = Bands(moderate=25, high=35, severe=45, no_travel=55, no_travel_inclusive=True)
RAIN_BANDS = Bands(moderate=0.10, high=0.25, severe=0.50, no_travel=1.00, no_travel_inclusive=False)
SNOW_BANDS = Bands(moderate=0.5, high=1.0, severe=2.0, no_travel=3.0, no_travel_inclusive=False)

# Load rules: heavier loads escalate the wind risk.
NO_TRAVEL_WIND_LOAD_LB = 30_000  # 45-54 mph with a load above this -> No Travel
SEVERE_WIND_LOAD_LB = 40_000  # 35-44 mph with a load above this -> Severe


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    wind_level: RiskLevel
    rain_level: RiskLevel
    snow_level: RiskLevel
    reasons: list = field(default_factory=list)

    def to_dict(self):
        return {
            "level": self.level.label,
            "score": int(self.level),
            "wind_level": self.wind_level.label,
            "rain_level": self.rain_level.label,
            "snow_level": self.snow_level.label,
            "reasons": list(self.reasons),
        }


def classify(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    """Classify one checkpoint: the worst of wind/rain/snow, escalated by the load rules."""
    if load_lb < 0:
        raise ValueError(f"load cannot be negative, got {load_lb}")

    wind_level = WIND_BANDS.classify(wind_mph)
    rain_level = RAIN_BANDS.classify(rain_in_hr)
    snow_level = SNOW_BANDS.classify(snow_in_hr)
    level = max(wind_level, rain_level, snow_level)

    reasons = []
    if wind_level > RiskLevel.LOW:
        reasons.append(f"Wind {wind_mph:.1f} mph: {wind_level.label}")
    if rain_level > RiskLevel.LOW:
        reasons.append(f"Rain {rain_in_hr:.2f} in/hr: {rain_level.label}")
    if snow_level > RiskLevel.LOW:
        reasons.append(f"Snow {snow_in_hr:.2f} in/hr: {snow_level.label}")

    # Load rules only ever raise the level. Wind >= 55 mph is already No Travel
    # for any load via WIND_BANDS.
    if wind_level == RiskLevel.SEVERE and load_lb > NO_TRAVEL_WIND_LOAD_LB:
        level = RiskLevel.NO_TRAVEL
        reasons.append(
            f"Load {load_lb:,.0f} lb > {NO_TRAVEL_WIND_LOAD_LB:,} lb in 45-54 mph wind: No Travel"
        )
    elif wind_level == RiskLevel.HIGH and load_lb > SEVERE_WIND_LOAD_LB and level < RiskLevel.SEVERE:
        level = RiskLevel.SEVERE
        reasons.append(
            f"Load {load_lb:,.0f} lb > {SEVERE_WIND_LOAD_LB:,} lb in 35-44 mph wind: Severe"
        )

    return RiskAssessment(
        level=level,
        wind_level=wind_level,
        rain_level=rain_level,
        snow_level=snow_level,
        reasons=reasons,
    )
