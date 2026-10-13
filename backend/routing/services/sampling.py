"""Place weather checkpoints along a route and compute the truck's ETA at each."""

from dataclasses import dataclass
from datetime import timedelta

from . import geo

SAMPLING_INTERVALS_MI = (10, 25, 50)


@dataclass(frozen=True)
class Checkpoint:
    index: int
    mile: float
    lat: float
    lon: float
    eta: object  # aware datetime
    # The stretch of road this checkpoint's weather stands for: halfway to each
    # neighbour, clipped to the route. Together they cover the route exactly once.
    covers_from_mi: float
    covers_to_mi: float

    @property
    def covered_miles(self):
        return self.covers_to_mi - self.covers_from_mi


def checkpoint_miles(distance_mi, interval_mi):
    """0, I, 2I, ... along the route, plus the destination."""
    miles = [k * interval_mi for k in range(int(distance_mi // interval_mi) + 1)]
    if distance_mi - miles[-1] > 1e-6:
        miles.append(distance_mi)
    return miles


def place_checkpoints(route, departure, interval_mi):
    miles = checkpoint_miles(route.distance_miles, interval_mi)
    checkpoints = []
    for i, mile in enumerate(miles):
        lat, lon = geo.point_at(route.points, route.cum_miles, mile)
        elapsed_s = geo.value_at(route.cum_miles, route.cum_seconds, mile)
        checkpoints.append(
            Checkpoint(
                index=i,
                mile=mile,
                lat=lat,
                lon=lon,
                eta=departure + timedelta(seconds=elapsed_s),
                covers_from_mi=(miles[i - 1] + mile) / 2 if i > 0 else 0.0,
                covers_to_mi=(mile + miles[i + 1]) / 2 if i + 1 < len(miles) else mile,
            )
        )
    return checkpoints
