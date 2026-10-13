import { memo } from 'react'
import type { Recommendation } from '../types'

function RecommendationPanel({ recommendation }: { recommendation: Recommendation }) {
  return (
    <section className="recommendation" aria-live="polite">
      <h2>{recommendation.headline}</h2>
      {recommendation.reasons.length > 0 && (
        <ul>
          {recommendation.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {recommendation.warning && (
        <p className="warning" role="alert">
          {recommendation.warning}
        </p>
      )}
    </section>
  )
}

// Memoised: the parent re-renders on every forecast-slider step.
export default memo(RecommendationPanel)
