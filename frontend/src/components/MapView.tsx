import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import type { Feature, FeatureCollection, LineString, Point } from 'geojson'
import { useEffect, useMemo, useRef, useState } from 'react'
import { escapeHtml, formatDuration, formatMiles, formatTime } from '../lib/format'
import { cumulativeMiles, haversineMi, lngLatAt, slice } from '../lib/geo'
import { LEVEL_COLORS, METRICS, heatmapColorRamp, routeColor } from '../lib/risk'
import type { Checkpoint, Heatmap, HeatmapMetric, Plan, PlannedRoute } from '../types'

// MapLibre locates its worker relative to its own module, which Vite's bundling
// breaks; serve the worker as an asset and point MapLibre at it instead.
maplibregl.setWorkerUrl(workerUrl)

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron'
const FIRST_LABEL_LAYER = 'waterway_line_label'
const FONT = ['Noto Sans Bold']
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

const NO_WEBGL_MESSAGE =
  'The map needs WebGL2, which this browser has turned off or does not support. ' +
  'Enable hardware acceleration in your browser settings (or try another browser) and reload. ' +
  'Route results in the sidebar still work.'

function hasWebGL2(): boolean {
  try {
    return !!document.createElement('canvas').getContext('webgl2')
  } catch {
    return false
  }
}

/** Radius of each heatmap point in grid cells; see heatmapIntensity. */
const HEAT_RADIUS_CELLS = 2

export interface CheckpointFocus {
  routeId: string
  index: number
  /** Changes on every request so focusing the same checkpoint twice still works. */
  nonce: number
}

interface Props {
  plan: Plan | null
  selectedId: string | null
  onSelectRoute: (routeId: string) => void
  focus: CheckpointFocus | null
  heatmap: Heatmap | null
  heatmapVisible: boolean
  metric: HeatmapMetric
  hour: number
}

type Coords = [number, number]

function featureCollection<G extends LineString | Point>(features: Feature<G>[]): FeatureCollection<G> {
  return { type: 'FeatureCollection', features }
}

function setData(map: maplibregl.Map, sourceId: string, data: FeatureCollection) {
  const source = map.getSource(sourceId) as maplibregl.GeoJSONSource | undefined
  source?.setData(data)
}

/** Static map data derived from a plan: route lines, risk-coloured stretches, checkpoints. */
function buildPlanLayers(plan: Plan) {
  const routes: Feature<LineString>[] = []
  const riskStretches: Feature<LineString>[] = []
  const checkpoints: Feature<Point>[] = []

  plan.routes.forEach((route, i) => {
    const color = routeColor(i)
    routes.push({
      type: 'Feature',
      properties: { id: route.id, color },
      geometry: { type: 'LineString', coordinates: route.geometry.map(([lat, lon]) => [lon, lat]) },
    })

    // Checkpoint coverage is in the server's miles along the full-resolution route;
    // rescale to the simplified geometry's own length before slicing it.
    const axis = cumulativeMiles(route.geometry)
    const scale = axis[axis.length - 1] / (route.distance_mi || 1)
    for (const cp of route.checkpoints) {
      riskStretches.push({
        type: 'Feature',
        properties: { routeId: route.id, color: LEVEL_COLORS[cp.level] },
        geometry: {
          type: 'LineString',
          coordinates: slice(route.geometry, axis, cp.covers_from_mi * scale, cp.covers_to_mi * scale),
        },
      })
      checkpoints.push({
        type: 'Feature',
        properties: { routeId: route.id, index: cp.index, color: LEVEL_COLORS[cp.level], score: cp.score ?? -1 },
        geometry: { type: 'Point', coordinates: [cp.lon, cp.lat] },
      })
    }
  })

  const endpoints = featureCollection<Point>([
    {
      type: 'Feature',
      properties: { label: 'Start' },
      geometry: { type: 'Point', coordinates: [plan.origin.lon, plan.origin.lat] },
    },
    {
      type: 'Feature',
      properties: { label: 'End' },
      geometry: { type: 'Point', coordinates: [plan.destination.lon, plan.destination.lat] },
    },
  ])

  return {
    routes: featureCollection(routes),
    riskStretches: featureCollection(riskStretches),
    checkpoints: featureCollection(checkpoints),
    endpoints,
  }
}

/** Heatmap points carrying every hour of every metric, so the slider only swaps an expression. */
function buildHeatFeatures(heatmap: Heatmap): FeatureCollection<Point> {
  const metrics = Object.keys(METRICS) as HeatmapMetric[]
  return featureCollection(
    heatmap.points.map(([lat, lon], p) => {
      const properties: Record<string, number | null> = {}
      for (const metric of metrics) {
        const key = METRICS[metric].key
        heatmap[metric][p].forEach((value, h) => {
          properties[`${key}${h}`] = value
        })
      }
      return { type: 'Feature', properties, geometry: { type: 'Point', coordinates: [lon, lat] } }
    }),
  )
}

/**
 * MapLibre's heatmap sums Gaussian kernels (sigma = radius / 3, peak 0.399 * weight).
 * Over a grid with spacing cx by cy pixels that sum is about 0.2785 * k^2 * cy / cx for
 * radius k * cy. Dividing by it makes density equal to a cell's own weight, so the
 * colour ramp can use the real thresholds. On Web Mercator cy / cx = 1 / cos(lat).
 */
function heatmapIntensity(centerLat: number): number {
  return Math.cos((centerLat * Math.PI) / 180) / (0.2785 * HEAT_RADIUS_CELLS ** 2)
}

function heatmapRadius(cellDeg: number, centerLat: number): ExpressionSpecification {
  // Pixels per grid cell (north-south) at zoom 0 with 512 px tiles.
  const cellPx = ((cellDeg * 512) / 360 / Math.cos((centerLat * Math.PI) / 180)) * HEAT_RADIUS_CELLS
  return ['interpolate', ['exponential', 2], ['zoom'], 0, cellPx, 16, cellPx * 2 ** 16]
}

function popupHtml(routeId: string, cp: Checkpoint): string {
  const color = LEVEL_COLORS[cp.level]
  const c = cp.conditions
  const conditions = c
    ? `<dl>
        <dt>Wind</dt><dd>${c.wind_mph.toFixed(0)} mph <span class="muted">(gusts ${c.gust_mph.toFixed(0)})</span></dd>
        <dt>Rain</dt><dd>${c.rain_in_hr.toFixed(2)} in/hr</dd>
        <dt>Snow</dt><dd>${c.snow_in_hr.toFixed(2)} in/hr</dd>
      </dl>`
    : '<p class="muted">No forecast covers this arrival time.</p>'
  const reasons = cp.reasons.length
    ? `<ul>${cp.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join('')}</ul>`
    : ''
  return `<div class="cp-popup">
    <div class="cp-head">Route ${escapeHtml(routeId)} · mile ${Math.round(cp.mile)}</div>
    <div class="cp-level" style="color:${color}"><i style="background:${color}"></i>${escapeHtml(cp.level)}</div>
    <div class="cp-eta">ETA ${escapeHtml(formatTime(cp.eta))}</div>
    ${conditions}${reasons}
  </div>`
}

/** One-line checkpoint summary shown on hover. */
function hoverHtml(routeId: string, cp: Checkpoint): string {
  const color = LEVEL_COLORS[cp.level]
  return `<div class="cp-hover-body">
    <span class="cp-hover-dot" style="background:${color}"></span>
    <b>${escapeHtml(routeId)} · mi ${Math.round(cp.mile)}</b>
    <span>ETA ${escapeHtml(formatTime(cp.eta))}</span>
    <b style="color:${color}">${escapeHtml(cp.level)}</b>
  </div>`
}

/** Route summary card placed on the map. */
function routeLabelHtml(route: PlannedRoute, color: string): string {
  const { miles_by_level: miles, max_level: worst } = route.summary
  const severe = miles.Severe + miles['No Travel']
  const worstColor = worst ? LEVEL_COLORS[worst] : LEVEL_COLORS.Unknown
  return `<div class="rl-head">
      <span class="rl-id" style="background:${color}">${escapeHtml(route.id)}</span>
      <b>${escapeHtml(formatDuration(route.duration_s))}</b>
      <span class="rl-muted">${escapeHtml(formatMiles(route.distance_mi))}</span>
      ${route.recommended ? '<span class="rl-best">Best</span>' : ''}
    </div>
    <div class="rl-risk">
      <span class="rl-dot" style="background:${worstColor}"></span>
      worst ${escapeHtml(worst ?? 'Unknown')} · Severe+ ${Math.round(severe)} mi · High ${Math.round(miles.High)} mi
    </div>`
}

/**
 * Where to pin each route's label: the point 20-80% along the route that is
 * farthest from the other routes, so labels sit on their own line and don't stack.
 */
function labelAnchors(plan: Plan): Coords[] {
  const samples = plan.routes.map((route) => {
    const step = Math.max(1, Math.floor(route.geometry.length / 80))
    return route.geometry.filter((_, i) => i % step === 0)
  })
  return samples.map((own, r) => {
    let best = own[Math.floor(own.length / 2)]
    if (samples.length > 1) {
      let bestDistance = -1
      for (let i = Math.floor(own.length * 0.2); i < Math.ceil(own.length * 0.8); i++) {
        const [lat, lon] = own[i]
        let nearest = Infinity
        samples.forEach((other, o) => {
          if (o !== r) for (const [oLat, oLon] of other) nearest = Math.min(nearest, haversineMi(lat, lon, oLat, oLon))
        })
        if (nearest > bestDistance) {
          bestDistance = nearest
          best = own[i]
        }
      }
    }
    return [best[1], best[0]]
  })
}

function addLayers(map: maplibregl.Map) {
  for (const id of ['heat', 'routes', 'risk', 'checkpoints', 'endpoints', 'trucks']) {
    map.addSource(id, { type: 'geojson', data: EMPTY })
  }
  const beforeLabels = map.getLayer(FIRST_LABEL_LAYER) ? FIRST_LABEL_LAYER : undefined

  map.addLayer(
    {
      id: 'heat',
      type: 'heatmap',
      source: 'heat',
      paint: { 'heatmap-opacity': 0.85, 'heatmap-weight': 0 },
    },
    beforeLabels,
  )
  map.addLayer({
    id: 'route-casing',
    type: 'line',
    source: 'routes',
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': '#ffffff', 'line-width': 9, 'line-opacity': 0.9 },
  })
  map.addLayer({
    id: 'route-line',
    type: 'line',
    source: 'routes',
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': 4, 'line-opacity': 0.55 },
  })
  map.addLayer({
    id: 'route-risk',
    type: 'line',
    source: 'risk',
    layout: { 'line-join': 'round', 'line-cap': 'butt' },
    paint: { 'line-color': ['get', 'color'], 'line-width': 4 },
    filter: ['==', ['get', 'routeId'], ''],
  })
  map.addLayer({
    id: 'checkpoints',
    type: 'circle',
    source: 'checkpoints',
    paint: {
      'circle-color': ['get', 'color'],
      'circle-radius': 3,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1,
    },
  })
  map.addLayer({
    id: 'endpoints',
    type: 'circle',
    source: 'endpoints',
    paint: {
      'circle-color': '#111827',
      'circle-radius': 7,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2.5,
    },
  })
  map.addLayer({
    id: 'endpoint-labels',
    type: 'symbol',
    source: 'endpoints',
    layout: {
      'text-field': ['get', 'label'],
      'text-font': FONT,
      'text-size': 12,
      'text-offset': [0, 1.3],
      'text-anchor': 'top',
    },
    paint: { 'text-color': '#111827', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 },
  })
  map.addLayer({
    id: 'trucks',
    type: 'circle',
    source: 'trucks',
    paint: {
      'circle-color': ['get', 'color'],
      'circle-radius': 10,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2.5,
      'circle-opacity': ['case', ['get', 'arrived'], 0.55, 1],
    },
  })
  map.addLayer({
    id: 'truck-labels',
    type: 'symbol',
    source: 'trucks',
    layout: {
      'text-field': ['get', 'id'],
      'text-font': FONT,
      'text-size': 11,
      'text-allow-overlap': true,
      'text-ignore-placement': true,
    },
    paint: { 'text-color': '#ffffff' },
  })
}

export default function MapView({ plan, selectedId, onSelectRoute, focus, heatmap, heatmapVisible, metric, hour }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const labelsRef = useRef<{ id: string; marker: maplibregl.Marker }[]>([])
  const showPopupRef = useRef<(routeId: string, index: number, at: maplibregl.LngLatLike) => void>(() => {})
  const [ready, setReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(() => (hasWebGL2() ? null : NO_WEBGL_MESSAGE))

  // Handlers registered once on the map read the latest props through refs.
  const planRef = useRef(plan)
  const onSelectRef = useRef(onSelectRoute)
  useEffect(() => {
    planRef.current = plan
    onSelectRef.current = onSelectRoute
  })

  const planLayers = useMemo(() => (plan ? buildPlanLayers(plan) : null), [plan])
  const heatFeatures = useMemo(() => (heatmap ? buildHeatFeatures(heatmap) : null), [heatmap])
  const routeAxes = useMemo(
    () => plan?.routes.map((route) => route.geometry.map((point) => point[2])) ?? [],
    [plan],
  )

  // Create the map once.
  useEffect(() => {
    if (mapError) return
    let map: maplibregl.Map
    try {
      map = new maplibregl.Map({
        container: containerRef.current!,
        style: STYLE_URL,
        center: [-98.5, 39.5],
        zoom: 3.3,
        attributionControl: { compact: true },
      })
    } catch {
      // WebGL can pass the support check yet still fail to start (e.g. a blocklisted GPU).
      // Reporting an external library's failure is a legitimate effect state update.
      // oxlint-disable-next-line react/set-state-in-effect
      setMapError(NO_WEBGL_MESSAGE)
      return
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.on('load', () => {
      addLayers(map)
      setReady(true)
    })

    // Hovering a checkpoint shows its ETA and level at a glance; clicking opens the details.
    const hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, className: 'cp-hover' })
    let hoveredKey = ''
    map.on('mousemove', 'checkpoints', (event) => {
      const feature = event.features?.[0]
      if (!feature) return
      const { routeId, index } = feature.properties as { routeId: string; index: number }
      const key = `${routeId}:${index}`
      if (key === hoveredKey) return
      const cp = planRef.current?.routes.find((r) => r.id === routeId)?.checkpoints[index]
      if (!cp) return
      hoveredKey = key
      hoverPopup.setLngLat((feature.geometry as Point).coordinates as Coords).setHTML(hoverHtml(routeId, cp)).addTo(map)
    })
    map.on('mouseleave', 'checkpoints', () => {
      hoveredKey = ''
      hoverPopup.remove()
    })

    const showPopup = (showPopupRef.current = (routeId, index, lngLat) => {
      const cp = planRef.current?.routes.find((r) => r.id === routeId)?.checkpoints[index]
      if (!cp) return
      hoveredKey = ''
      hoverPopup.remove()
      popupRef.current?.remove()
      popupRef.current = new maplibregl.Popup({ maxWidth: '280px', offset: 8 })
        .setLngLat(lngLat)
        .setHTML(popupHtml(routeId, cp))
        .addTo(map)
    })
    map.on('click', 'checkpoints', (event) => {
      const feature = event.features?.[0]
      if (!feature) return
      const { routeId, index } = feature.properties as { routeId: string; index: number }
      onSelectRef.current(routeId)
      showPopup(routeId, index, (feature.geometry as Point).coordinates as Coords)
    })
    map.on('click', 'route-line', (event) => {
      // A checkpoint click also hits the line underneath; let the checkpoint handler win.
      if (map.queryRenderedFeatures(event.point, { layers: ['checkpoints'] }).length) return
      const id = event.features?.[0]?.properties?.id
      if (id) onSelectRef.current(id)
    })
    for (const layer of ['checkpoints', 'route-line']) {
      map.on('mouseenter', layer, () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', layer, () => (map.getCanvas().style.cursor = ''))
    }

    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
      setReady(false)
    }
  }, [mapError])

  // New plan: replace route data and frame it.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    popupRef.current?.remove()
    setData(map, 'routes', planLayers?.routes ?? EMPTY)
    setData(map, 'risk', planLayers?.riskStretches ?? EMPTY)
    setData(map, 'checkpoints', planLayers?.checkpoints ?? EMPTY)
    setData(map, 'endpoints', planLayers?.endpoints ?? EMPTY)
    if (!plan) return
    const bounds = new maplibregl.LngLatBounds()
    for (const route of plan.routes) for (const [lat, lon] of route.geometry) bounds.extend([lon, lat])
    map.fitBounds(bounds, { padding: { top: 60, bottom: 150, left: 50, right: 60 }, duration: 900 })
  }, [ready, plan, planLayers])

  // Route summary cards pinned to each route; clicking one selects that route.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !plan) return
    const anchors = labelAnchors(plan)
    const labels = plan.routes.map((route, i) => {
      const element = document.createElement('button')
      element.type = 'button'
      element.className = 'route-label'
      element.setAttribute('aria-label', `Select route ${route.id}`)
      element.innerHTML = routeLabelHtml(route, routeColor(i))
      element.addEventListener('click', (event) => {
        event.stopPropagation()
        onSelectRef.current(route.id)
      })
      const marker = new maplibregl.Marker({ element, anchor: 'bottom', offset: [0, -8] }).setLngLat(anchors[i]).addTo(map)
      return { id: route.id, marker }
    })
    labelsRef.current = labels
    return () => {
      for (const { marker } of labels) marker.remove()
      labelsRef.current = []
    }
  }, [ready, plan])

  useEffect(() => {
    for (const { id, marker } of labelsRef.current) {
      const element = marker.getElement()
      const selected = id === selectedId
      element.classList.toggle('selected', selected)
      element.style.zIndex = selected ? '2' : '1'
    }
  }, [selectedId, plan, ready])

  // Selected route: draw it on top, risk-coloured, with prominent checkpoints.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const sel = selectedId ?? ''
    const isSelected: ExpressionSpecification = ['==', ['get', 'id'], sel]
    const cpSelected: ExpressionSpecification = ['==', ['get', 'routeId'], sel]
    map.setLayoutProperty('route-line', 'line-sort-key', ['case', isSelected, 1, 0])
    map.setLayoutProperty('route-casing', 'line-sort-key', ['case', isSelected, 1, 0])
    map.setPaintProperty('route-line', 'line-width', ['case', isSelected, 10, 4])
    map.setPaintProperty('route-line', 'line-opacity', ['case', isSelected, 1, 0.5])
    map.setPaintProperty('route-casing', 'line-width', ['case', isSelected, 14, 8])
    map.setFilter('route-risk', ['==', ['get', 'routeId'], sel])
    map.setLayoutProperty('checkpoints', 'circle-sort-key', ['case', cpSelected, ['+', 10, ['get', 'score']], ['get', 'score']])
    // Dots grow with zoom so 10-mile spacing doesn't bury the route line when zoomed out.
    map.setPaintProperty('checkpoints', 'circle-radius', [
      'interpolate', ['linear'], ['zoom'],
      4, ['case', cpSelected, 3, 1.5],
      9, ['case', cpSelected, 7, 4],
    ])
    map.setPaintProperty('checkpoints', 'circle-opacity', ['case', cpSelected, 1, 0.6])
    map.setPaintProperty('checkpoints', 'circle-stroke-opacity', ['case', cpSelected, 1, 0.6])
  }, [ready, selectedId, plan])

  // Fly to a checkpoint picked from the sidebar and open its popup.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !focus) return
    const cp = planRef.current?.routes.find((r) => r.id === focus.routeId)?.checkpoints[focus.index]
    if (!cp) return
    map.flyTo({ center: [cp.lon, cp.lat], zoom: Math.max(map.getZoom(), 7), duration: 900 })
    showPopupRef.current(focus.routeId, focus.index, [cp.lon, cp.lat])
  }, [ready, focus])

  // New heatmap grid: upload once; size the kernel to the grid spacing.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    setData(map, 'heat', heatFeatures ?? EMPTY)
    if (!heatmap || !heatmap.points.length) return
    const centerLat = heatmap.points.reduce((sum, [lat]) => sum + lat, 0) / heatmap.points.length
    map.setPaintProperty('heat', 'heatmap-radius', heatmapRadius(heatmap.cell_deg, centerLat))
    map.setPaintProperty('heat', 'heatmap-intensity', heatmapIntensity(centerLat))
  }, [ready, heatmap, heatFeatures])

  // Slider and layer changes only swap paint expressions; no data is re-uploaded.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const { key, max } = METRICS[metric]
    map.setPaintProperty('heat', 'heatmap-weight', ['/', ['coalesce', ['get', `${key}${hour}`], 0], max])
    map.setPaintProperty('heat', 'heatmap-color', heatmapColorRamp(metric))
  }, [ready, metric, hour])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    map.setLayoutProperty('heat', 'visibility', heatmapVisible ? 'visible' : 'none')
  }, [ready, heatmapVisible])

  // Where each truck is at the slider's hour, from the per-point elapsed seconds.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    if (!plan) {
      setData(map, 'trucks', EMPTY)
      return
    }
    const elapsed = hour * 3600
    const trucks = plan.routes.map((route, i): Feature<Point> => ({
      type: 'Feature',
      properties: { id: route.id, color: routeColor(i), arrived: elapsed >= route.duration_s },
      geometry: { type: 'Point', coordinates: lngLatAt(route.geometry, routeAxes[i], elapsed) },
    }))
    setData(map, 'trucks', featureCollection(trucks))
  }, [ready, plan, routeAxes, hour])

  return (
    <>
      <div ref={containerRef} className="map" />
      {mapError && (
        <div className="map-error" role="alert">
          <strong>Map unavailable</strong>
          <p>{mapError}</p>
        </div>
      )}
    </>
  )
}
