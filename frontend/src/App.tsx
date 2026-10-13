import { useCallback, useRef, useState } from 'react'
import { ApiError, fetchHeatmap, fetchPlan } from './api'
import MapView, { type CheckpointFocus } from './components/MapView'
import RecommendationPanel from './components/RecommendationPanel'
import RouteCards from './components/RouteCards'
import TimeControls from './components/TimeControls'
import TripForm from './components/TripForm'
import type { Heatmap, HeatmapMetric, Plan, PlanRequest } from './types'

type HeatmapStatus = 'idle' | 'loading' | 'ready' | 'error'

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Something went wrong. Please try again.'
}

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

export default function App() {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [planning, setPlanning] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [focus, setFocus] = useState<CheckpointFocus | null>(null)

  const [heatmap, setHeatmap] = useState<Heatmap | null>(null)
  const [heatmapStatus, setHeatmapStatus] = useState<HeatmapStatus>('idle')
  const [heatmapError, setHeatmapError] = useState<string | null>(null)
  const [heatmapVisible, setHeatmapVisible] = useState(true)
  const [metric, setMetric] = useState<HeatmapMetric>('risk')
  const [hour, setHour] = useState(0)
  const [playing, setPlaying] = useState(false)

  const requestRef = useRef<AbortController | null>(null)

  // Stable identity so the memoised form doesn't re-render as the slider moves.
  const handleSubmit = useCallback(async (request: PlanRequest) => {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller

    setPlanning(true)
    setPlanError(null)
    setPlaying(false)
    let result: Plan
    try {
      result = await fetchPlan(request, controller.signal)
    } catch (error) {
      if (isAbort(error)) return
      setPlanError(errorMessage(error))
      setPlanning(false)
      return
    }
    setPlan(result)
    setSelectedId(result.recommendation.route_id)
    setFocus(null)
    setHour(0)
    setHeatmap(null)
    setHeatmapError(null)
    setPlanning(false)
    // On narrow screens the map sits below the form; bring it into view.
    if (window.matchMedia('(max-width: 860px)').matches) {
      document.querySelector('.map-area')?.scrollIntoView({ behavior: 'smooth' })
    }

    // Routes show immediately; the heatmap grid follows in the background.
    setHeatmapStatus('loading')
    try {
      setHeatmap(await fetchHeatmap(result, controller.signal))
      setHeatmapStatus('ready')
    } catch (error) {
      if (isAbort(error)) return
      setHeatmapError(errorMessage(error))
      setHeatmapStatus('error')
    }
  }, [])

  const focusCheckpoint = useCallback((routeId: string, index: number) => {
    setSelectedId(routeId)
    setFocus({ routeId, index, nonce: Date.now() })
  }, [])

  return (
    <div className="app">
      <aside className="sidebar">
        <header className="brand">
          <h1>Weather-Aware Truck Routing</h1>
          <p>Three truck routes, forecast weather at your arrival time, and the safest pick.</p>
        </header>
        <TripForm busy={planning} onSubmit={handleSubmit} />
        {planError && (
          <p className="error" role="alert">
            {planError}
          </p>
        )}
        {plan && selectedId && (
          <>
            <RecommendationPanel recommendation={plan.recommendation} />
            <RouteCards plan={plan} selectedId={selectedId} onSelect={setSelectedId} onFocusCheckpoint={focusCheckpoint} />
            <p className="footnote">
              Weather is sampled every {plan.interval_mi} miles at the truck's estimated arrival time for a{' '}
              {plan.load_lb.toLocaleString()} lb load. Ranking: fewest No Travel, Severe and High miles, then lowest
              average risk, then shortest time.
            </p>
          </>
        )}
      </aside>
      <main className="map-area">
        <MapView
          plan={plan}
          selectedId={selectedId}
          onSelectRoute={setSelectedId}
          focus={focus}
          heatmap={heatmap}
          heatmapVisible={heatmapVisible}
          metric={metric}
          hour={hour}
        />
        {planning && <div className="map-overlay">Fetching routes and forecasts…</div>}
        {plan && (
          <TimeControls
            departure={plan.departure}
            hour={hour}
            maxHour={heatmap?.hours ?? 48}
            onHourChange={setHour}
            playing={playing}
            onPlayingChange={setPlaying}
            metric={metric}
            onMetricChange={setMetric}
            heatmapVisible={heatmapVisible}
            onHeatmapVisibleChange={setHeatmapVisible}
            heatmapStatus={heatmapStatus}
            heatmapError={heatmapError}
          />
        )}
      </main>
    </div>
  )
}
