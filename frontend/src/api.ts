import type { Heatmap, Place, Plan, PlanRequest } from './types'

const BASE = (import.meta.env.VITE_API_URL || '/api').replace(/\/$/, '')

export class ApiError extends Error {}

/** Turn a DRF error body ({detail} or {field: [messages]}) into one readable line. */
function describeError(body: unknown, status: number): string {
  if (body && typeof body === 'object') {
    if ('detail' in body && typeof body.detail === 'string') return body.detail
    const parts = Object.entries(body).map(([field, messages]) => {
      const text = Array.isArray(messages) ? messages.join(' ') : String(messages)
      return `${field.replace(/_/g, ' ')}: ${text}`
    })
    if (parts.length) return parts.join(' ')
  }
  return `Request failed (${status}).`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(BASE + path, init)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('Cannot reach the server. Is the backend running?')
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(describeError(body, response.status))
  return body as T
}

function postJson<T>(path: string, payload: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
}

export function geocode(query: string, signal?: AbortSignal): Promise<Place[]> {
  return request<Place[]>(`/geocode/?q=${encodeURIComponent(query)}`, { signal })
}

export function fetchPlan(params: PlanRequest, signal?: AbortSignal): Promise<Plan> {
  return postJson<Plan>('/plan/', params, signal)
}

export function fetchHeatmap(plan: Plan, signal?: AbortSignal): Promise<Heatmap> {
  return postJson<Heatmap>(
    '/heatmap/',
    {
      // Drop the per-point seconds; the heatmap only needs the line shapes.
      routes: plan.routes.map((route) => route.geometry.map(([lat, lon]) => [lat, lon])),
      departure: plan.departure,
      load_lb: plan.load_lb,
    },
    signal,
  )
}
