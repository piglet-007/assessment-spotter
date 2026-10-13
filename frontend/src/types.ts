// Shapes returned by the Django API (see backend/routing/serializers.py).

export type RiskLevel = 'Low' | 'Moderate' | 'High' | 'Severe' | 'No Travel'
export type Level = RiskLevel | 'Unknown'

export interface Place {
  label: string
  lat: number
  lon: number
}

export interface Conditions {
  wind_mph: number
  gust_mph: number
  rain_in_hr: number
  snow_in_hr: number
}

export interface Checkpoint {
  index: number
  mile: number
  lat: number
  lon: number
  eta: string
  covers_from_mi: number
  covers_to_mi: number
  conditions: Conditions | null
  level: Level
  score: number | null
  reasons: string[]
}

export interface RouteSummary {
  miles_by_level: Record<Level, number>
  avg_risk: number | null
  max_level: RiskLevel | null
}

export interface PlannedRoute {
  id: string
  rank: number
  recommended: boolean
  source: 'provider' | 'detour'
  distance_mi: number
  duration_s: number
  arrival: string
  /** [lat, lon, seconds since departure] */
  geometry: [number, number, number][]
  summary: RouteSummary
  checkpoints: Checkpoint[]
}

export interface Recommendation {
  route_id: string
  ranking: string[]
  headline: string
  reasons: string[]
  warning: string | null
}

export interface Plan {
  origin: Place
  destination: Place
  departure: string
  load_lb: number
  interval_mi: number
  routes: PlannedRoute[]
  recommendation: Recommendation
}

export interface PlanRequest {
  /** A picked place, or free text the server geocodes. */
  origin: Place | string
  destination: Place | string
  departure: string
  load_lb: number
  interval_mi: number
}

export type HeatmapMetric = 'risk' | 'wind_mph' | 'rain_in_hr' | 'snow_in_hr'

export interface Heatmap {
  start: string
  hours: number
  cell_deg: number
  points: [number, number][]
  risk: (number | null)[][]
  wind_mph: (number | null)[][]
  rain_in_hr: (number | null)[][]
  snow_in_hr: (number | null)[][]
}
