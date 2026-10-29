"""
SUDHARSARTHI - Service Area & Geographical Boundary Configuration.

Supports nationwide reporting across India, with primary smart-city pilot
focus in the Ghaziabad & Duhai Metropolitan NCR Corridor (including Duhai,
Muradnagar, Raj Nagar, Sanjay Nagar, Govindpuram, Kavi Nagar, Mohan Nagar,
Sahibabad, Indirapuram, and Crossings Republik).

Coordinates and boundaries are configurable via environment variables.
"""
import math
import os

AREA_NAME = os.environ.get("SERVICE_AREA_NAME", "Ghaziabad & Duhai Region (All-India Enabled)")
FOCAL_AREA_NAME = os.environ.get("FOCAL_AREA_NAME", "Ghaziabad & Duhai Municipal Cluster")

# Center point for initial map view (Duhai-Ghaziabad central corridor)
DEFAULT_CENTER_LAT = float(os.environ.get("SERVICE_CENTER_LAT", 28.6950))
DEFAULT_CENTER_LNG = float(os.environ.get("SERVICE_CENTER_LNG", 77.4450))
CENTER = (DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG)

# Primary Pilot / Focal Service Boundary: Ghaziabad & Duhai NCR Corridor
GHAZIABAD_DUHAI_BOUNDS = (
    float(os.environ.get("FOCAL_BOUNDS_S", 28.5800)),
    float(os.environ.get("FOCAL_BOUNDS_W", 77.3300)),
    float(os.environ.get("FOCAL_BOUNDS_N", 28.8200)),
    float(os.environ.get("FOCAL_BOUNDS_E", 77.5600)),
)

# Supported Boundary for Reporting: Nationwide across India
# South: 6.0 (Kanyakumari / Great Nicobar), West: 68.0 (Gujarat), North: 38.0 (Kashmir), East: 98.0 (Arunachal Pradesh)
BOUNDS = (
    float(os.environ.get("SERVICE_BOUNDS_S", 6.0)),
    float(os.environ.get("SERVICE_BOUNDS_W", 68.0)),
    float(os.environ.get("SERVICE_BOUNDS_N", 38.0)),
    float(os.environ.get("SERVICE_BOUNDS_E", 98.0)),
)


def inside(lat, lng):
    """Return True if the given (lat, lng) coordinates fall within the supported reporting boundary (India)."""
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    if math.isnan(lat) or math.isinf(lat) or math.isnan(lng) or math.isinf(lng):
        return False
    s, w, n, e = BOUNDS
    return s <= lat <= n and w <= lng <= e


def is_focal_area(lat, lng):
    """Return True if coordinates fall within the primary Ghaziabad & Duhai municipal pilot cluster."""
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    if math.isnan(lat) or math.isinf(lat) or math.isnan(lng) or math.isinf(lng):
        return False
    s, w, n, e = GHAZIABAD_DUHAI_BOUNDS
    return s <= lat <= n and w <= lng <= e


def get_bounds_dict():
    s, w, n, e = BOUNDS
    fs, fw, fn, fe = GHAZIABAD_DUHAI_BOUNDS
    return {
        "south": s,
        "west": w,
        "north": n,
        "east": e,
        "center": {"lat": CENTER[0], "lng": CENTER[1]},
        "areaName": AREA_NAME,
        "focalArea": {
            "name": FOCAL_AREA_NAME,
            "south": fs,
            "west": fw,
            "north": fn,
            "east": fe,
            "center": {"lat": CENTER[0], "lng": CENTER[1]},
        },
    }

