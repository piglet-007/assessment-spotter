from datetime import datetime, timedelta, timezone

from rest_framework import serializers

from .services import geo
from .services.sampling import SAMPLING_INTERVALS_MI

MAX_LOAD_LB = 80_000
MAX_DEPARTURE_AHEAD = timedelta(days=14)
DEPARTURE_GRACE = timedelta(hours=1)
# About 30 m: invisible at map zoom levels, cuts route payloads roughly tenfold.
GEOMETRY_TOLERANCE_DEG = 0.0003


class PlaceField(serializers.Field):
    """Either a free-text place name or {"lat", "lon", "label"?}."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            if len(data.strip()) < 2:
                raise serializers.ValidationError("Enter a place name.")
            return {"query": data.strip()}
        if isinstance(data, dict):
            try:
                lat, lon = float(data["lat"]), float(data["lon"])
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError("Expected numeric lat and lon.")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise serializers.ValidationError("Coordinates out of range.")
            return {"lat": lat, "lon": lon, "label": str(data.get("label") or f"{lat:.4f}, {lon:.4f}")}
        raise serializers.ValidationError("Expected a place name or {lat, lon}.")


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceField()
    destination = PlaceField()
    departure = serializers.DateTimeField()
    load_lb = serializers.FloatField(min_value=0, max_value=MAX_LOAD_LB)
    interval_mi = serializers.ChoiceField(choices=SAMPLING_INTERVALS_MI, default=25)

    def validate_departure(self, value):
        now = datetime.now(timezone.utc)
        if value < now - DEPARTURE_GRACE:
            raise serializers.ValidationError("Departure time is in the past.")
        if value > now + MAX_DEPARTURE_AHEAD:
            raise serializers.ValidationError("Departure must be within 14 days (forecast range).")
        return value.astimezone(timezone.utc)


def route_name(index):
    return "ABC"[index] if index < 3 else str(index + 1)


def serialize_checkpoint(result):
    cp = result.checkpoint
    return {
        "index": cp.index,
        "mile": round(cp.mile, 1),
        "lat": round(cp.lat, 5),
        "lon": round(cp.lon, 5),
        "eta": cp.eta.replace(microsecond=0).isoformat(),
        "covers_from_mi": round(cp.covers_from_mi, 1),
        "covers_to_mi": round(cp.covers_to_mi, 1),
        "conditions": result.conditions.to_dict() if result.conditions else None,
        "level": result.level_label,
        "score": int(result.risk.level) if result.risk else None,
        "reasons": list(result.risk.reasons) if result.risk else [],
    }


def serialize_route(assessment, name, rank, departure):
    route = assessment.route
    keep = geo.simplify(route.points, GEOMETRY_TOLERANCE_DEG)
    return {
        "id": name,
        "rank": rank,
        "recommended": rank == 1,
        "source": route.source,
        "distance_mi": round(route.distance_miles, 1),
        "duration_s": round(route.duration_s),
        "arrival": (departure + timedelta(seconds=round(route.duration_s))).isoformat(),
        # [lat, lon, seconds since departure] per point, so the client can place
        # the truck at any moment without another request.
        "geometry": [
            [round(route.points[i][0], 5), round(route.points[i][1], 5), round(route.cum_seconds[i])]
            for i in keep
        ],
        "summary": {
            "miles_by_level": {k: round(v, 1) for k, v in assessment.miles_by_level.items()},
            "avg_risk": round(assessment.avg_risk, 3) if assessment.avg_risk is not None else None,
            "max_level": assessment.max_level.label if assessment.max_level is not None else None,
        },
        "checkpoints": [serialize_checkpoint(r) for r in assessment.checkpoints],
    }
