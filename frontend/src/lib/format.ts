export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? `${h} h ${m.toString().padStart(2, '0')} min` : `${m} min`
}

const timeFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
})

export function formatTime(iso: string | Date): string {
  return timeFormat.format(typeof iso === 'string' ? new Date(iso) : iso)
}

export function formatMiles(miles: number): string {
  return `${Math.round(miles).toLocaleString()} mi`
}

/** Value for a <input type="datetime-local">, in the browser's time zone. */
export function toLocalInputValue(date: Date): string {
  const pad = (n: number) => n.toString().padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}
