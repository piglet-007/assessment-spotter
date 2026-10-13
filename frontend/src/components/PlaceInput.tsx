import { useEffect, useId, useRef, useState } from 'react'
import { geocode } from '../api'
import type { Place } from '../types'

interface Props {
  label: string
  placeholder: string
  text: string
  onTextChange: (text: string) => void
  onPick: (place: Place) => void
}

const DEBOUNCE_MS = 300
const MIN_QUERY_LENGTH = 3

/** Text input with place suggestions. Typing clears the picked place; picking fills the text. */
export default function PlaceInput({ label, placeholder, text, onTextChange, onPick }: Props) {
  const id = useId()
  const [suggestions, setSuggestions] = useState<Place[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const skipNextLookup = useRef(false)

  useEffect(() => {
    if (skipNextLookup.current) {
      skipNextLookup.current = false
      return
    }
    const query = text.trim()
    if (query.length < MIN_QUERY_LENGTH) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      geocode(query, controller.signal)
        .then((places) => {
          setSuggestions(places)
          setActive(-1)
        })
        .catch(() => setSuggestions([]))
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [text])

  function pick(place: Place) {
    skipNextLookup.current = true
    onTextChange(place.label)
    onPick(place)
    setOpen(false)
    setSuggestions([])
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || !suggestions.length) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((i) => (i + 1) % suggestions.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => (i <= 0 ? suggestions.length - 1 : i - 1))
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault()
      pick(suggestions[active])
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  const listId = `${id}-list`
  // Suggestions for an earlier, longer query are hidden rather than cleared in the effect.
  const showList = open && text.trim().length >= MIN_QUERY_LENGTH && suggestions.length > 0

  return (
    <div className="field place-input">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="text"
        autoComplete="off"
        placeholder={placeholder}
        value={text}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        onChange={(event) => {
          onTextChange(event.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={onKeyDown}
      />
      {showList && (
        <ul id={listId} role="listbox" className="suggestions">
          {suggestions.map((place, i) => (
            <li
              key={`${place.lat},${place.lon}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'active' : undefined}
              onMouseDown={(event) => {
                event.preventDefault()
                pick(place)
              }}
            >
              {place.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
