import { memo } from 'react'
import { formatDuration, formatMiles, formatTime } from '../lib/format'
import { LEVEL_COLORS, RISK_LEVELS, routeColor } from '../lib/risk'
import type { Checkpoint, Level, Plan, PlannedRoute } from '../types'

interface Props {
  plan: Plan
  selectedId: string
  onSelect: (routeId: string) => void
  onFocusCheckpoint: (routeId: string, index: number) => void
}

const LEVELS_WITH_UNKNOWN: Level[] = [...RISK_LEVELS, 'Unknown']
const MAX_HAZARDS = 6

function RiskBar({ route }: { route: PlannedRoute }) {
  const total = route.distance_mi || 1
  return (
    <div className="risk-bar" role="img" aria-label={describeMiles(route)}>
      {LEVELS_WITH_UNKNOWN.map((level) => {
        const miles = route.summary.miles_by_level[level]
        if (!miles) return null
        return (
          <span
            key={level}
            style={{ width: `${(miles / total) * 100}%`, background: LEVEL_COLORS[level] }}
            title={`${level}: ${formatMiles(miles)}`}
          />
        )
      })}
    </div>
  )
}

function describeMiles(route: PlannedRoute): string {
  return LEVELS_WITH_UNKNOWN.filter((level) => route.summary.miles_by_level[level] > 0)
    .map((level) => `${level} ${formatMiles(route.summary.miles_by_level[level])}`)
    .join(' · ')
}

function worstCheckpoints(route: PlannedRoute): Checkpoint[] {
  return route.checkpoints
    .filter((cp) => cp.score !== null && cp.score > 0)
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0) || a.mile - b.mile)
    .slice(0, MAX_HAZARDS)
    .sort((a, b) => a.mile - b.mile)
}

function RouteCards({ plan, selectedId, onSelect, onFocusCheckpoint }: Props) {
  const byRank = [...plan.routes].sort((a, b) => a.rank - b.rank)
  return (
    <ul className="route-cards">
      {byRank.map((route) => {
        const index = plan.routes.indexOf(route)
        const selected = route.id === selectedId
        const hazards = selected ? worstCheckpoints(route) : []
        const { miles_by_level: miles, avg_risk: avg, max_level: worst } = route.summary
        return (
          <li key={route.id}>
            <div
              className={`route-card${selected ? ' selected' : ''}`}
              style={{ '--route-color': routeColor(index) } as React.CSSProperties}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              onClick={() => onSelect(route.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onSelect(route.id)
                }
              }}
            >
              <div className="route-card-head">
                <span className="route-swatch" />
                <strong>Route {route.id}</strong>
                {route.recommended ? (
                  <span className="badge recommended">Recommended</span>
                ) : (
                  <span className="badge">#{route.rank}</span>
                )}
                {worst && (
                  <span className="worst" style={{ color: LEVEL_COLORS[worst] }}>
                    worst: {worst}
                  </span>
                )}
              </div>
              <div className="route-stats">
                <span>{formatMiles(route.distance_mi)}</span>
                <span>{formatDuration(route.duration_s)}</span>
                <span>arrive {formatTime(route.arrival)}</span>
              </div>
              <RiskBar route={route} />
              <div className="route-risk-numbers">
                <span>
                  Severe+ <b>{formatMiles(miles.Severe + miles['No Travel'])}</b>
                </span>
                <span>
                  High <b>{formatMiles(miles.High)}</b>
                </span>
                <span>
                  Avg risk <b>{avg === null ? '—' : avg.toFixed(2)}</b>
                </span>
              </div>
              {selected && (
                <div className="hazards">
                  {hazards.length ? (
                    <>
                      <div className="hazards-title">Hazards along this route</div>
                      <ul>
                        {hazards.map((cp) => (
                          <li key={cp.index}>
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation()
                                onFocusCheckpoint(route.id, cp.index)
                              }}
                            >
                              <span className="dot" style={{ background: LEVEL_COLORS[cp.level] }} />
                              <span className="hazard-mile">mi {Math.round(cp.mile)}</span>
                              <span className="hazard-time">{formatTime(cp.eta)}</span>
                              <span className="hazard-level">
                                {/* The last reason is the one that set the final level (load rules come last). */}
                                <b style={{ color: LEVEL_COLORS[cp.level] }}>{cp.level}</b> · {cp.reasons.at(-1)}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <div className="hazards-title">No weather above Low risk at the planned times.</div>
                  )}
                </div>
              )}
              {route.source === 'detour' && (
                <div className="note">Alternative generated via a detour waypoint.</div>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

// Memoised: the parent re-renders on every forecast-slider step.
export default memo(RouteCards)
