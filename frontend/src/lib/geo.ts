const EARTH_RADIUS_MI = 3958.7613
const toRad = (deg: number) => (deg * Math.PI) / 180

export function haversineMi(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const h =
    Math.sin(toRad(lat2 - lat1) / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(toRad(lon2 - lon1) / 2) ** 2
  return 2 * EARTH_RADIUS_MI * Math.asin(Math.sqrt(h))
}

/** Miles from the start to each point of a [lat, lon, ...] polyline. */
export function cumulativeMiles(points: readonly (readonly number[])[]): number[] {
  const cum = [0]
  for (let i = 1; i < points.length; i++) {
    const [lat1, lon1] = points[i - 1]
    const [lat2, lon2] = points[i]
    cum.push(cum[i - 1] + haversineMi(lat1, lon1, lat2, lon2))
  }
  return cum
}

/** Index i and fraction f such that target lies f of the way from values[i] to values[i+1]. */
function locate(values: number[], target: number): [number, number] {
  if (target <= values[0]) return [0, 0]
  const last = values.length - 1
  if (target >= values[last]) return [last - 1, 1]
  let lo = 0
  let hi = last
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (values[mid] <= target) lo = mid
    else hi = mid
  }
  const span = values[hi] - values[lo]
  return [lo, span ? (target - values[lo]) / span : 0]
}

/** [lon, lat] at a position along a polyline, measured on the given cumulative axis. */
export function lngLatAt(points: readonly (readonly number[])[], axis: number[], target: number): [number, number] {
  const [i, f] = locate(axis, target)
  const [lat1, lon1] = points[i]
  const [lat2, lon2] = points[i + 1]
  return [lon1 + (lon2 - lon1) * f, lat1 + (lat2 - lat1) * f]
}

/** [lon, lat] coordinates of the polyline between two positions on the axis. */
export function slice(
  points: readonly (readonly number[])[],
  axis: number[],
  from: number,
  to: number,
): [number, number][] {
  const coords: [number, number][] = [lngLatAt(points, axis, from)]
  for (let i = 0; i < points.length; i++) {
    if (axis[i] > from && axis[i] < to) coords.push([points[i][1], points[i][0]])
  }
  coords.push(lngLatAt(points, axis, to))
  return coords
}
