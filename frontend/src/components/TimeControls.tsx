import { useEffect } from 'react'
import { formatTime } from '../lib/format'
import { LEVEL_COLORS, METRICS, RISK_LEVELS } from '../lib/risk'
import type { HeatmapMetric } from '../types'

interface Props {
  departure: string
  hour: number
  maxHour: number
  onHourChange: (hour: number) => void
  playing: boolean
  onPlayingChange: (playing: boolean) => void
  metric: HeatmapMetric
  onMetricChange: (metric: HeatmapMetric) => void
  heatmapVisible: boolean
  onHeatmapVisibleChange: (visible: boolean) => void
  heatmapStatus: 'idle' | 'loading' | 'ready' | 'error'
  heatmapError: string | null
}

const PLAY_STEP_MS = 450

export default function TimeControls(props: Props) {
  const { departure, hour, maxHour, onHourChange, playing, onPlayingChange, metric } = props
  const at = new Date(new Date(departure).getTime() + hour * 3600 * 1000)
  const scale = METRICS[metric]

  useEffect(() => {
    if (!playing) return
    const timer = window.setInterval(() => {
      onHourChange(hour >= maxHour ? 0 : hour + 1)
    }, PLAY_STEP_MS)
    return () => window.clearInterval(timer)
  }, [playing, hour, maxHour, onHourChange])

  return (
    <div className="time-controls">
      <div className="time-row">
        <button
          type="button"
          className="play"
          aria-label={playing ? 'Pause forecast animation' : 'Play forecast animation'}
          onClick={() => onPlayingChange(!playing)}
        >
          {playing ? '❚❚' : '▶'}
        </button>
        <input
          type="range"
          min={0}
          max={maxHour}
          step={1}
          value={hour}
          aria-label="Forecast hour after departure"
          aria-valuetext={`${hour} hours after departure, ${formatTime(at)}`}
          onChange={(event) => onHourChange(Number(event.target.value))}
        />
        <div className="time-label">
          <strong>+{hour} h</strong>
          <span>{formatTime(at)}</span>
        </div>
      </div>
      <div className="time-row secondary">
        <label className="toggle">
          <input
            type="checkbox"
            checked={props.heatmapVisible}
            onChange={(event) => props.onHeatmapVisibleChange(event.target.checked)}
          />
          Heatmap
        </label>
        <div className="segmented small" role="radiogroup" aria-label="Heatmap layer">
          {(Object.keys(METRICS) as HeatmapMetric[]).map((key) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={metric === key}
              className={metric === key ? 'on' : undefined}
              onClick={() => props.onMetricChange(key)}
            >
              {METRICS[key].label}
            </button>
          ))}
        </div>
        <div className="legend" aria-label="Risk colours">
          {RISK_LEVELS.slice(1).map((level, i) => (
            <span key={level}>
              <i style={{ background: LEVEL_COLORS[level] }} />
              {metric === 'risk' ? level : `≥${scale.thresholds[i]}`}
            </span>
          ))}
          {metric !== 'risk' && <span className="unit">{scale.unit}</span>}
        </div>
        {props.heatmapStatus === 'loading' && <span className="muted">Loading forecast grid…</span>}
        {props.heatmapStatus === 'error' && <span className="error-text">{props.heatmapError}</span>}
      </div>
    </div>
  )
}
