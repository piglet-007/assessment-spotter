import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'
import type { HeatmapMetric, Level, RiskLevel } from '../types'

export const RISK_LEVELS: RiskLevel[] = ['Low', 'Moderate', 'High', 'Severe', 'No Travel']

export const LEVEL_COLORS: Record<Level, string> = {
  Low: '#2f9e5a',
  Moderate: '#e0b100',
  High: '#ef7d14',
  Severe: '#d62839',
  'No Travel': '#6b1f8f',
  Unknown: '#9aa1a9',
}

// Route identity colours, chosen to stay distinct from the risk colours.
const ROUTE_COLOR_LIST = ['#2563eb', '#0e7490', '#475569']

export function routeColor(index: number): string {
  return ROUTE_COLOR_LIST[index % ROUTE_COLOR_LIST.length]
}

export interface MetricScale {
  label: string
  unit: string
  /** Value treated as "full strength" on the heatmap. */
  max: number
  /** Lower bounds of Moderate, High, Severe and No Travel in this metric's units. */
  thresholds: [number, number, number, number]
  /** Short key prefix used for per-hour feature properties. */
  key: string
}

export const METRICS: Record<HeatmapMetric, MetricScale> = {
  risk: { label: 'Risk (with load)', unit: '', max: 4, thresholds: [1, 2, 3, 4], key: 'r' },
  wind_mph: { label: 'Wind', unit: 'mph', max: 60, thresholds: [25, 35, 45, 55], key: 'w' },
  rain_in_hr: { label: 'Rain', unit: 'in/hr', max: 1.2, thresholds: [0.1, 0.25, 0.5, 1.0], key: 'p' },
  snow_in_hr: { label: 'Snow', unit: 'in/hr', max: 3.5, thresholds: [0.5, 1, 2, 3], key: 's' },
}

function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

/**
 * Heatmap colour ramp over density 0..1, where density ~ value / max.
 * Below the Moderate threshold the map stays nearly clear so routes remain readable.
 */
export function heatmapColorRamp(metric: HeatmapMetric): ExpressionSpecification {
  const { max, thresholds } = METRICS[metric]
  const [moderate, high, severe, noTravel] = thresholds.map((t) => Math.min(t / max, 0.999))
  return [
    'interpolate',
    ['linear'],
    ['heatmap-density'],
    0,
    'rgba(0, 0, 0, 0)',
    moderate * 0.5,
    withAlpha(LEVEL_COLORS.Low, 0.12),
    moderate,
    withAlpha(LEVEL_COLORS.Moderate, 0.45),
    high,
    withAlpha(LEVEL_COLORS.High, 0.55),
    severe,
    withAlpha(LEVEL_COLORS.Severe, 0.62),
    noTravel,
    withAlpha(LEVEL_COLORS['No Travel'], 0.7),
  ]
}
