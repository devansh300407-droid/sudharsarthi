"""
SUDHARSARTHI - Service Area & Geographical Boundary Configuration.

Covers the practical Ghaziabad & Duhai Municipal Metropolitan Region,
including Duhai, Muradnagar, Raj Nagar, Sanjay Nagar, Govindpuram,
Kavi Nagar, Mohan Nagar, Sahibabad, Indirapuram, and Crossings Republik.

All coordinates are configurable via environment variables if custom
boundaries are needed for other municipal deployments.
"""
import math
import os

AREA_NAME = os.environ.get("SERVICE_AREA_NAME", "Ghaziabad & Duhai Region")

# Center point for initial map view (Duhai-Ghaziabad central corridor)
DEFAULT_CENTER_LAT = float(os.environ.get("SERVICE_CENTER_LAT", 28.6950))
DEFAULT_CENTER_LNG = float(os.environ.get("SERVICE_CENTER_LNG", 77.4450))
CENTER = (DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG)

# Service boundary: south, west, north, east
# Covers ~26 km x 26 km across Ghaziabad & Duhai NCR cluster
BOUNDS = (
    float(os.environ.get("SERVICE_BOUNDS_S", 28.5800)),
    float(os.environ.get("SERVICE_BOUNDS_W", 77.3300)),
    float(os.environ.get("SERVICE_BOUNDS_N", 28.8200)),
    float(os.environ.get("SERVICE_BOUNDS_E", 77.5600)),
)


def inside(lat, lng):
    """Return True if the given (lat, lng) coordinates fall within the supported boundary."""
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    if math.isnan(lat) or math.isinf(lat) or math.isnan(lng) or math.isinf(lng):
        return False
    s, w, n, e = BOUNDS
    return s <= lat <= n and w <= lng <= e


def get_bounds_dict():
    s, w, n, e = BOUNDS
    return {
        "south": s,
        "west": w,
        "north": n,
        "east": e,
        "center": {"lat": CENTER[0], "lng": CENTER[1]},
        "areaName": AREA_NAME,
    }
