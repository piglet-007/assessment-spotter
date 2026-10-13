from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import PlanRequestSerializer, route_name, serialize_route
from .services.assessment import assess_routes
from .services.errors import ProviderError
from .services.here import HereClient
from .services.recommend import recommend
from .services.routing import plan_routes
from .services.weather import OpenMeteoClient


def provider_error(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


@api_view(["GET"])
def geocode(request):
    query = request.query_params.get("q", "").strip()
    if len(query) < 3:
        return Response([])
    try:
        return Response(HereClient().geocode(query))
    except ProviderError as exc:
        return provider_error(exc)


def resolve_place(client, place, field):
    if "query" not in place:
        return place
    matches = client.geocode(place["query"], limit=1)
    if not matches:
        raise ProviderError(f"Could not find {field} \"{place['query']}\".")
    return matches[0]


@api_view(["POST"])
def plan(request):
    params = PlanRequestSerializer(data=request.data)
    params.is_valid(raise_exception=True)
    data = params.validated_data
    departure, load_lb, interval_mi = data["departure"], data["load_lb"], data["interval_mi"]

    try:
        here = HereClient()
        origin = resolve_place(here, data["origin"], "origin")
        destination = resolve_place(here, data["destination"], "destination")
        routes = plan_routes(
            here, (origin["lat"], origin["lon"]), (destination["lat"], destination["lon"]), departure, load_lb
        )
        assessments = assess_routes(routes, departure, load_lb, interval_mi, OpenMeteoClient())
    except ProviderError as exc:
        return provider_error(exc)

    # Routes come back fastest first; their letters follow that order.
    names = {id(a): route_name(i) for i, a in enumerate(assessments)}
    result = recommend(assessments, lambda a: names[id(a)])
    ranks = {id(a): i + 1 for i, a in enumerate(result.ranked)}

    return Response(
        {
            "origin": origin,
            "destination": destination,
            "departure": departure.isoformat(),
            "load_lb": load_lb,
            "interval_mi": interval_mi,
            "routes": [serialize_route(a, names[id(a)], ranks[id(a)], departure) for a in assessments],
            "recommendation": {
                "route_id": names[id(result.ranked[0])],
                "ranking": [names[id(a)] for a in result.ranked],
                "headline": result.headline,
                "reasons": result.reasons,
                "warning": result.warning,
            },
        }
    )
