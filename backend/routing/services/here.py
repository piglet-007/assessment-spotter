"""Thin client for the HERE Geocoding and Routing v8 APIs."""

import hashlib
import json

import requests
from django.conf import settings
from django.core.cache import cache

from .errors import ProviderError

GEOCODE_URL = "https://geocode.search.hereapi.com/v1/geocode"
ROUTES_URL = "https://router.hereapi.com/v8/routes"

GEOCODE_CACHE_SECONDS = 24 * 3600
ROUTES_CACHE_SECONDS = 15 * 60


class HereClient:
    def __init__(self, api_key=None, session=None, timeout=30):
        self.api_key = api_key or settings.HERE_API_KEY
        if not self.api_key:
            raise ProviderError("HERE_API_KEY is not configured on the server.")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, url, params, cache_seconds):
        key = "here:" + hashlib.sha256(
            json.dumps([url, params], sort_keys=True, default=str).encode()
        ).hexdigest()
        cached = cache.get(key)
        if cached is not None:
            return cached

        try:
            response = self.session.get(
                url, params={**params, "apiKey": self.api_key}, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Could not reach HERE: {exc.__class__.__name__}") from exc
        if not response.ok:
            try:
                detail = response.json().get("title", "")
            except ValueError:
                detail = ""
            raise ProviderError(f"HERE request failed ({response.status_code}) {detail}".strip())

        data = response.json()
        cache.set(key, data, cache_seconds)
        return data

    def geocode(self, query, limit=5):
        data = self._get(
            GEOCODE_URL,
            {"q": query, "limit": limit, "in": "countryCode:USA,CAN"},
            GEOCODE_CACHE_SECONDS,
        )
        return [
            {
                "label": item["address"]["label"],
                "lat": item["position"]["lat"],
                "lon": item["position"]["lng"],
            }
            for item in data.get("items", [])
        ]

    def truck_routes(self, origin, destination, departure, gross_weight_kg, alternatives=2, via=None):
        """Raw HERE route objects for a truck. origin/destination/via are (lat, lon)."""
        params = {
            "transportMode": "truck",
            "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
            "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
            "departureTime": departure.isoformat(),
            "vehicle[grossWeight]": int(gross_weight_kg),
            "return": "polyline,summary",
            # segmentId splits spans at every road segment, so per-span durations
            # reflect real road speeds rather than one average for the whole trip.
            "spans": "segmentId,length,duration",
            "alternatives": alternatives,
        }
        if via:
            params["via"] = f"{via[0]:.6f},{via[1]:.6f}!passThrough=true"

        data = self._get(ROUTES_URL, params, ROUTES_CACHE_SECONDS)
        routes = data.get("routes", [])
        if not routes:
            notices = "; ".join(n.get("title", "") for n in data.get("notices", []))
            raise ProviderError(f"No truck route found. {notices}".strip())
        return routes
