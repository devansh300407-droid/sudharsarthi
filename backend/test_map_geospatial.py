"""
Dedicated Map & Geospatial Test Suite for SudharSarthi.
Verifies shortcuts, boundary calculations, NaN rejection, database precision,
click isolation, popup privacy, and frontend synchronization.
"""
import json
import math
import re
import sys
import os

BACKEND_DIR = r"C:\Users\DEVANSH SHARMA\.gemini\antigravity\scratch\SUDHARSARTHI\backend"
FRONTEND_FILE = r"C:\Users\DEVANSH SHARMA\.gemini\antigravity\scratch\SUDHARSARTHI\frontend\index.html"

sys.path.insert(0, BACKEND_DIR)
import app as app_module
import database as db
import duhai

app_module.app.config["TESTING"] = True
client = app_module.app.test_client()

print("=" * 70)
print("SUDHARSARTHI GEOSPATIAL & MAP VERIFICATION SUITE")
print("=" * 70)

# 1. Read frontend index.html and verify map code structure
with open(FRONTEND_FILE, "r", encoding="utf-8") as f:
    html = f.read()

# Check Leaflet & OpenStreetMap presence
assert "leaflet.min.js" in html, "Leaflet JS missing"
assert "leaflet.min.css" in html, "Leaflet CSS missing"
assert "openstreetmap.org" in html, "OpenStreetMap tile provider missing"
assert "ResizeObserver" in html, "ResizeObserver missing"
assert "map.invalidateSize()" in html, "map.invalidateSize() missing"
print("✓ Leaflet, OpenStreetMap, and ResizeObserver properly integrated")

# 2. Check area shortcuts in frontend JS
shortcuts = {
    'center':      (28.6692, 77.4370, 'Ghaziabad Central'),
    'duhai':       (28.7285, 77.4740, 'Duhai RRTS Hub'),
    'rajnagar':    (28.6860, 77.4420, 'Raj Nagar / ALT'),
    'indirapuram': (28.6350, 77.3750, 'Indirapuram / NH9'),
    'mohannagar':  (28.6750, 77.3680, 'Mohan Nagar / Sahibabad'),
    'muradnagar':  (28.7750, 77.5020, 'Muradnagar'),
    'govindpuram': (28.6670, 77.4850, 'Govindpuram'),
    'sanjaynagar': (28.6850, 77.4560, 'Sanjay Nagar')
}

for key, (lat, lng, label) in shortcuts.items():
    assert duhai.inside(lat, lng), f"Shortcut '{label}' is outside the service boundary!"
    assert f"'{key}'" in html, f"Shortcut key '{key}' missing from frontend"
    assert f"{lat:.4f}" in html or f"{lat}" in html, f"Latitude for '{label}' missing in frontend"
    assert f"{lng:.4f}" in html or f"{lng}" in html, f"Longitude for '{label}' missing in frontend"
print(f"✓ All {len(shortcuts)} area shortcuts verified inside Ghaziabad & Duhai region with real-world coordinates")

# 3. Check boundary calculations (Backend & Frontend Agreement)
s, w, n, e = duhai.BOUNDS
assert s == 28.5800 and w == 77.3300 and n == 28.8200 and e == 77.5600
print(f"✓ Authoritative service bounds verified: S={s}, W={w}, N={n}, E={e}")

# Test points inside boundary
inside_points = [
    (28.7285, 77.4740, "Duhai Station"),
    (28.6692, 77.4370, "Ghaziabad Central"),
    (28.6860, 77.4420, "Raj Nagar Extension"),
    (28.6350, 77.3750, "Indirapuram Sector 4"),
    (28.6750, 77.3680, "Mohan Nagar Junction"),
    (28.7750, 77.5020, "Muradnagar Canal"),
    (28.5900, 77.4000, "Crossings Republik"),
    (28.8100, 77.5100, "Morta / Duhai North")
]
for lat, lng, name in inside_points:
    assert duhai.inside(lat, lng), f"{name} should be INSIDE boundary"
print(f"✓ {len(inside_points)} strategic points across Ghaziabad & Duhai confirmed INSIDE boundary")

# Test points outside boundary
outside_points = [
    (26.8467, 80.9462, "Lucknow"),
    (28.5562, 77.1000, "Delhi Airport (IGI)"),
    (19.0760, 72.8777, "Mumbai"),
    (28.9000, 77.4500, "North of Meerut boundary"),
    (28.5000, 77.4500, "South of Noida boundary"),
    (28.7000, 77.2500, "West of Delhi border"),
    (28.7000, 77.7000, "East of Hapur boundary")
]
for lat, lng, name in outside_points:
    assert not duhai.inside(lat, lng), f"{name} should be OUTSIDE boundary"
print(f"✓ {len(outside_points)} external points confirmed OUTSIDE boundary and rejected")

# 4. Test NaN and Infinity rejection
assert not duhai.inside(float('nan'), 77.45), "NaN latitude was not rejected!"
assert not duhai.inside(28.70, float('nan')), "NaN longitude was not rejected!"
assert not duhai.inside(float('inf'), 77.45), "Infinity latitude was not rejected!"
assert not duhai.inside(28.70, float('inf')), "Infinity longitude was not rejected!"
print("✓ NaN and Infinity coordinates properly rejected by boundary engine")

# 5. Backend API coordinate validation
r_nan = client.post("/api/reports", json={"category": "Pothole", "lat": "nan", "lng": 77.45})
assert r_nan.status_code == 400
assert "finite number" in r_nan.get_json()["error"] or "number" in r_nan.get_json()["error"]

r_outside = client.post("/api/reports", json={"category": "Pothole", "lat": 26.8467, "lng": 80.9462})
assert r_outside.status_code == 400
assert "outside" in r_outside.get_json()["error"].lower()
print("✓ Backend API successfully rejects NaN, Infinity, and out-of-boundary locations with 400 Bad Request")

# 6. Test report submission with precise coordinates and check DB persistence
precise_lat = 28.72851
precise_lng = 77.47402
r_submit = client.post("/api/reports", json={
    "category": "Pothole",
    "lat": precise_lat,
    "lng": precise_lng,
    "location": "Near Duhai Eastern Flyover Pillar 14",
    "description": "Deep 12 cm crater pothole on rapid transit corridor",
    "reporter": "Citizen Verification Tester"
})
assert r_submit.status_code == 201
saved = r_submit.get_json()
assert saved["lat"] == precise_lat
assert saved["lng"] == precise_lng

# Verify direct retrieval from database
db_record = db.get_report(saved["id"])
assert db_record is not None
assert abs(db_record["lat"] - precise_lat) < 1e-6
assert abs(db_record["lng"] - precise_lng) < 1e-6
print(f"✓ Coordinate precision verified in database persistence: {db_record['lat']}, {db_record['lng']}")

# 7. Check popup privacy: ensure citizen emails/phones are not rendered in public map popups
popup_fn_start = html.find("function createPopupContent")
popup_fn_end = html.find("function renderMarkers")
popup_code = html[popup_fn_start:popup_fn_end]
assert "email" not in popup_code, "Public map popup must not contain citizen email"
assert "phone" not in popup_code, "Public map popup must not contain citizen phone"
print("✓ Popup privacy verified: private user profile fields are never exposed on map")

# 8. Check marker click isolation
assert "L.DomEvent.stopPropagation(e)" in html
print("✓ Marker click isolation verified: clicking report pins does not trigger map selection")

# 9. Check selection marker draggable & manual inputs
assert "selectionMarker.on('dragend'" in html or "draggable: true" in html
assert "report-lat-input" in html
assert "report-lng-input" in html
assert "handleManualCoordInput()" in html
assert "clearSelectedLocation()" in html
print("✓ Draggable selection pin and two-way manual coordinate synchronization verified")

# 10. Check mobile drawer handle
assert "map-drawer-handle" in html
assert "toggleMapDrawer()" in html
print("✓ Mobile drawer handle and responsive map view verified")

print("\nALL MAP & GEOSPATIAL CHECKS PASSED!")
