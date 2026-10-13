import { memo, useState } from 'react'
import { toLocalInputValue } from '../lib/format'
import type { Place, PlanRequest } from '../types'
import PlaceInput from './PlaceInput'

interface Props {
  busy: boolean
  onSubmit: (request: PlanRequest) => void
}

const INTERVALS = [10, 25, 50]
const MAX_LOAD_LB = 80_000

function defaultDeparture(): string {
  const date = new Date()
  date.setHours(date.getHours() + 1, 0, 0, 0)
  return toLocalInputValue(date)
}

function TripForm({ busy, onSubmit }: Props) {
  const [originText, setOriginText] = useState('')
  const [origin, setOrigin] = useState<Place | null>(null)
  const [destinationText, setDestinationText] = useState('')
  const [destination, setDestination] = useState<Place | null>(null)
  const [departure, setDeparture] = useState(defaultDeparture)
  const [load, setLoad] = useState('40000')
  const [intervalMi, setIntervalMi] = useState(25)

  // Picker bounds as of page load; the server re-checks the departure on submit.
  const [[minDeparture, maxDeparture]] = useState(() => {
    const now = new Date()
    return [toLocalInputValue(now), toLocalInputValue(new Date(now.getTime() + 14 * 24 * 3600 * 1000))]
  })
  const loadNumber = Number(load)
  const loadValid = load !== '' && Number.isFinite(loadNumber) && loadNumber >= 0 && loadNumber <= MAX_LOAD_LB
  const canSubmit = originText.trim().length > 1 && destinationText.trim().length > 1 && departure && loadValid

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!canSubmit || busy) return
    onSubmit({
      // A picked suggestion is sent as coordinates; free text is geocoded by the server.
      origin: origin ?? originText.trim(),
      destination: destination ?? destinationText.trim(),
      departure: new Date(departure).toISOString(),
      load_lb: loadNumber,
      interval_mi: intervalMi,
    })
  }

  return (
    <form className="trip-form" onSubmit={submit}>
      <PlaceInput
        label="Origin"
        placeholder="e.g. Dallas, TX"
        text={originText}
        onTextChange={(text) => {
          setOriginText(text)
          setOrigin(null)
        }}
        onPick={setOrigin}
      />
      <PlaceInput
        label="Destination"
        placeholder="e.g. Denver, CO"
        text={destinationText}
        onTextChange={(text) => {
          setDestinationText(text)
          setDestination(null)
        }}
        onPick={setDestination}
      />
      <div className="field">
        <label htmlFor="departure">Departure (your local time)</label>
        <input
          id="departure"
          type="datetime-local"
          value={departure}
          min={minDeparture}
          max={maxDeparture}
          onChange={(event) => setDeparture(event.target.value)}
        />
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="load">Load weight (lb)</label>
          <input
            id="load"
            type="number"
            inputMode="numeric"
            min={0}
            max={MAX_LOAD_LB}
            step={500}
            value={load}
            aria-invalid={!loadValid}
            onChange={(event) => setLoad(event.target.value)}
          />
        </div>
        <div className="field">
          <span className="label" id="interval-label">
            Check weather every
          </span>
          <div className="segmented" role="radiogroup" aria-labelledby="interval-label">
            {INTERVALS.map((miles) => (
              <button
                key={miles}
                type="button"
                role="radio"
                aria-checked={intervalMi === miles}
                className={intervalMi === miles ? 'on' : undefined}
                onClick={() => setIntervalMi(miles)}
              >
                {miles} mi
              </button>
            ))}
          </div>
        </div>
      </div>
      {!loadValid && <p className="field-error">Load must be between 0 and 80,000 lb.</p>}
      <button type="submit" className="primary" disabled={!canSubmit || busy}>
        {busy ? 'Planning routes…' : 'Find safest route'}
      </button>
    </form>
  )
}

// Memoised: the parent re-renders on every forecast-slider step.
export default memo(TripForm)
