"""
SUDHARSARTHI - Flask REST API.

Endpoints:
  GET   /api/health                  Service health, AI engine status, email mode
  GET   /api/config                  Service area bounds, center, categories, wards
  GET   /api/reports                 Filterable reports list (?category=&priority=&status=&ward=&search=)
  GET   /api/reports/<id>            Single report details
  POST  /api/reports                 Submit report (multipart or JSON)
  PATCH /api/reports/<id>/status     Update status/priority (Officer / Admin protected)
  POST  /api/ai/classify             AI photo/text urgency pre-scan
  GET   /api/analytics               City-wide civic & road safety analytics
  POST  /api/reports/rescore         Re-evaluate urgency scores based on aging & repeat complaints
  POST  /api/demo/reset              Reset demo data (Protected)
  GET   /uploads/<file>              Uploaded citizen photos

Citizen Authentication & Dashboard Endpoints:
  POST  /api/auth/register           Register citizen (Name, Email, Mobile, Password)
  POST  /api/auth/login              Citizen login (Email or Mobile + Password)
  POST  /api/auth/logout             Citizen logout
  GET   /api/auth/me                 Authenticated citizen profile
  GET   /api/citizen/reports         Authenticated citizen's own submitted reports
"""
import base64
import os
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import Flask, jsonify, make_response, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Minimal .env reader (KEY=value per line) so no extra package is needed."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                if value.strip():
                    os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(os.path.join(BASE_DIR, ".env"))

import database as db  # noqa: E402
import duhai  # noqa: E402
import notifier  # noqa: E402
import urgency_ai  # noqa: E402

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
_default_front = os.path.join(os.path.dirname(BASE_DIR), "frontend")
FRONTEND_DIR = os.environ.get("FRONTEND_DIR") or (_default_front if os.path.isdir(_default_front) else None)
STATUSES = ["Reported", "Under Review", "Repair in Progress", "Resolved"]
MAX_UPLOAD_MB = 5
SESSION_COOKIE_NAME = "sudharsarthi_session"
SESSION_DURATION_DAYS = 30
OFFICER_KEY = os.environ.get("OFFICER_API_KEY", "sudharsarthi-officer-secret-key-2026")

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "sudharsarthi-secret-2026-secure-key-institute-level")
app.config["MAX_CONTENT_LENGTH"] = (MAX_UPLOAD_MB + 1) * 1024 * 1024
app.config["NOTIFY_WAIT"] = False
_create_lock = threading.Lock()

# Basic in-memory rate limiting for login attempts: ip/identifier -> list of failure timestamps
_failed_login_attempts = defaultdict(list)
_rate_limit_lock = threading.Lock()

os.makedirs(UPLOAD_DIR, exist_ok=True)
db.init_db()
if os.environ.get("SEED_DEMO", "1") == "1":
    db.seed_if_empty()


# ------------------------------------------------------------------ helpers

def bad(message, code=400):
    return jsonify({"success": False, "error": message}), code


@app.errorhandler(413)
def too_big(_):
    return bad(f"Photo too large (max {MAX_UPLOAD_MB} MB).", 413)


def _get_allowed_origin():
    """Determine the CORS origin to return based on the request Origin and CORS_ORIGIN configuration."""
    conf = os.environ.get("CORS_ORIGIN", "*")
    req_origin = request.headers.get("Origin")
    if not req_origin:
        return conf
    if conf == "*":
        # When supporting credentials, reflect request origin
        return req_origin
    allowed = [o.strip() for o in conf.split(",") if o.strip()]
    if req_origin in allowed or "*" in allowed:
        return req_origin
    return allowed[0] if allowed else req_origin


@app.before_request
def handle_options_preflight():
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = _get_allowed_origin()
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Auth-Token, X-Officer-Key, Accept"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
        return resp


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = _get_allowed_origin()
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Auth-Token, X-Officer-Key, Accept"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
    return resp


def get_current_user():
    """Extract authenticated citizen or officer from Authorization header or cookie."""
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.headers.get("X-Auth-Token", "").strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return db.get_session_user(token)


def is_officer_or_admin(user=None):
    """Check if current user or request header has officer/admin authority."""
    header_key = request.headers.get("X-Officer-Key", "").strip()
    if header_key and header_key == OFFICER_KEY:
        return True
    user = user or get_current_user()
    if user and user.get("role") in ("officer", "admin"):
        return True
    return False


def _image_ext(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def read_image():
    """Return (bytes, ext) from a multipart file or an `imageData` data-URL, else (None, None)."""
    raw = None
    upload = request.files.get("image") if request.files else None
    if upload:
        raw = upload.read()
    else:
        payload = request.get_json(silent=True) if request.is_json else request.form
        data_url = (payload or {}).get("imageData")
        if data_url:
            m = re.match(r"data:image/[a-z+.-]+;base64,(.+)$", data_url, re.DOTALL)
            if not m:
                raise ValueError("imageData must be a base64 image data-URL")
            raw = base64.b64decode(m.group(1))
    if not raw:
        return None, None
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"Photo too large (max {MAX_UPLOAD_MB} MB).")
    ext = _image_ext(raw)
    if not ext:
        raise ValueError("Photo must be a JPG, PNG or WebP image.")
    return raw, ext


def fields():
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def num(value, name, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' is required and must be a number.")
    if not lo <= v <= hi:
        raise ValueError(f"'{name}' must be between {lo} and {hi}.")
    return v


def days_since(date_text):
    try:
        return max(0, (datetime.now() - datetime.strptime(date_text[:10], "%Y-%m-%d")).days)
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------ auth routes

@app.post("/api/auth/register")
def register():
    data = fields()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = re.sub(r"\D", "", data.get("phone") or "")
    password = data.get("password") or ""
    confirm_password = data.get("confirmPassword") or ""

    # Validation
    if not name or len(name) < 2:
        return bad("Please provide your full name (at least 2 characters).")
    if len(name) > 80:
        return bad("Name cannot exceed 80 characters.")

    email_regex = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    if not re.match(email_regex, email):
        return bad("Please enter a valid email address.")

    if len(phone) == 12 and phone.startswith("91"):
        phone = phone[2:]
    if len(phone) != 10 or not phone[0] in "6789":
        return bad("Please enter a valid 10-digit Indian mobile number (e.g. 9876543210).")

    if len(password) < 8:
        return bad("Password must be at least 8 characters long.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return bad("Password must include at least one letter and one number.")
    if password != confirm_password:
        return bad("Passwords do not match. Please verify your password.")

    # Duplicate checks
    existing_by_email = db.get_user_auth(email)
    if existing_by_email:
        return bad("An account with this email address is already registered.", 409)
    existing_by_phone = db.get_user_auth(phone)
    if existing_by_phone:
        return bad("An account with this mobile number is already registered.", 409)

    password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    try:
        user = db.create_user(name, email, phone, password_hash, role="citizen")
    except Exception as exc:
        return bad(f"Registration failed: {str(exc)}", 500)

    # Establish persistent session
    token = str(uuid.uuid4())
    expires_at = (datetime.now() + timedelta(days=SESSION_DURATION_DAYS)).isoformat()
    db.create_session(user["id"], token, expires_at, ip=request.remote_addr,
                      user_agent=request.headers.get("User-Agent"))

    # Link historical reports where email or phone match (never name alone)
    db.link_historical_reports(user["id"], email, phone)

    resp = make_response(jsonify({
        "success": True,
        "message": "Registration successful! Welcome to SudharSarthi.",
        "user": user,
        "token": token,
    }), 201)

    # Set secure HTTP-only cookie
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_DURATION_DAYS * 86400,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure or os.environ.get("COOKIE_SECURE", "0") == "1",
        path="/",
    )
    return resp


@app.post("/api/auth/login")
def login():
    data = fields()
    identifier = (data.get("identifier") or data.get("email") or data.get("phone") or "").strip()
    password = data.get("password") or ""

    if not identifier or not password:
        return bad("Please enter your email/mobile and password.")

    # Basic rate limiting: max 5 failed attempts within 5 minutes per IP or identifier
    client_ip = request.remote_addr or "unknown"
    rate_key = f"{client_ip}:{identifier.lower()}"
    now_ts = time.time()
    with _rate_limit_lock:
        recent = [t for t in _failed_login_attempts[rate_key] if now_ts - t < 300]
        _failed_login_attempts[rate_key] = recent
        if len(recent) >= 5:
            return bad("Too many failed login attempts. Please wait 5 minutes before trying again.", 429)

    user_auth = db.get_user_auth(identifier)
    if not user_auth or not check_password_hash(user_auth["password_hash"], password):
        with _rate_limit_lock:
            _failed_login_attempts[rate_key].append(now_ts)
        return bad("Invalid email/mobile number or password.", 401)

    # Clear rate limit counter on success
    with _rate_limit_lock:
        _failed_login_attempts.pop(rate_key, None)

    db.update_user_last_login(user_auth["id"])
    safe_user = db.get_user_by_id(user_auth["id"])

    # Link historical reports where email or phone match
    db.link_historical_reports(safe_user["id"], safe_user["email"], safe_user["phone"])

    token = str(uuid.uuid4())
    expires_at = (datetime.now() + timedelta(days=SESSION_DURATION_DAYS)).isoformat()
    db.create_session(safe_user["id"], token, expires_at, ip=request.remote_addr,
                      user_agent=request.headers.get("User-Agent"))

    resp = make_response(jsonify({
        "success": True,
        "message": f"Welcome back, {safe_user['name']}!",
        "user": safe_user,
        "token": token,
    }))

    resp.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_DURATION_DAYS * 86400,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure or os.environ.get("COOKIE_SECURE", "0") == "1",
        path="/",
    )
    return resp


@app.post("/api/auth/logout")
def logout():
    # Invalidate both cookie session and header session if provided
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        db.delete_session(cookie_token)

    auth_header = request.headers.get("Authorization", "")
    header_token = None
    if auth_header.startswith("Bearer "):
        header_token = auth_header[7:].strip()
    if not header_token:
        header_token = request.headers.get("X-Auth-Token", "").strip()
    if header_token:
        db.delete_session(header_token)

    resp = make_response(jsonify({"success": True, "message": "Logged out successfully."}))
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp


@app.get("/api/auth/me")
def me():
    user = get_current_user()
    if not user:
        return bad("Authentication required. Please log in.", 401)
    return jsonify({"success": True, "user": user})


@app.get("/api/citizen/reports")
def citizen_reports():
    user = get_current_user()
    if not user:
        return bad("Please log in to view your reports.", 401)

    user_reports = db.list_reports_by_user(user["id"])
    open_count = sum(r["status"] != "Resolved" for r in user_reports)
    under_review_count = sum(r["status"] == "Under Review" for r in user_reports)
    repair_count = sum(r["status"] == "Repair in Progress" for r in user_reports)
    resolved_count = sum(r["status"] == "Resolved" for r in user_reports)

    return jsonify({
        "success": True,
        "citizen": user,
        "reports": user_reports,
        "stats": {
            "total": len(user_reports),
            "open": open_count,
            "underReview": under_review_count,
            "repairInProgress": repair_count,
            "resolved": resolved_count,
        },
    })


# ------------------------------------------------------------------ general routes

@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "serviceArea": duhai.AREA_NAME,
        "reports": db.count(),
        "aiMode": "claude-vision+rules" if urgency_ai.vision_enabled() else "rules-engine",
        "emailMode": "smtp" if os.environ.get("SMTP_HOST") else "outbox-files",
    })


@app.get("/api/config")
def config():
    return jsonify({
        "area": duhai.AREA_NAME,
        "center": duhai.CENTER,
        "bounds": duhai.BOUNDS,
        "categories": urgency_ai.CATEGORIES,
        "wards": [w["name"] for w in notifier._load_wards()],
        "wardDetails": notifier._load_wards(),
    })


@app.get("/api/reports")
def get_reports():
    a = request.args
    return jsonify(db.list_reports(
        a.get("category"), a.get("priority"), a.get("status"),
        a.get("ward"), a.get("search"), a.get("user_id")
    ))


@app.get("/api/reports/<report_id>")
def get_report(report_id):
    report = db.get_report(report_id)
    return jsonify(report) if report else bad("Report not found.", 404)


@app.post("/api/reports")
def create_report():
    current_user = get_current_user()
    try:
        data = fields()
        category = data.get("category", "")
        if category not in urgency_ai.CATEGORIES:
            raise ValueError("'category' must be one of: " + ", ".join(urgency_ai.CATEGORIES))
        lat = num(data.get("lat"), "lat", -90, 90)
        lng = num(data.get("lng"), "lng", -180, 180)
        if not duhai.inside(lat, lng):
            raise ValueError(
                f"This location is outside the supported service boundary. SUDHARSARTHI currently serves the {duhai.AREA_NAME}."
            )
        image_bytes, ext = read_image()
    except ValueError as exc:
        return bad(str(exc))

    description = (data.get("description") or "").strip()[:1000]
    location = (data.get("location") or "").strip()[:200] or f"{lat:.5f}, {lng:.5f}"
    landmark = (data.get("landmark") or "").strip()[:200]

    ward_name = data.get("ward")
    if not ward_name:
        w = notifier.nearest_ward(lat, lng)
        ward_name = w["name"] if w else "Unassigned"

    # Same issue already open within 25 m? Then this is a repeat complaint.
    nearby = db.find_nearby_open(lat, lng, category)

    assessment = urgency_ai.assess(
        image_bytes, category, description, location, landmark,
        repeat_count=len(nearby), age_days=0,
    )

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    # Determine reporter info and user ownership
    user_id = current_user["id"] if current_user else None
    if current_user:
        reporter_name = f"{current_user['name']} ({current_user['phone']})"
    else:
        reporter_name = (data.get("reporter") or "Citizen (Web Portal)")[:80]

    with _create_lock:
        report_id = db.next_id(now.year)
        image_url = (data.get("imageUrl") or "")
        if image_bytes:
            filename = f"{report_id}.{ext}"
            with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
                f.write(image_bytes)
            image_url = f"{request.host_url.rstrip('/')}/uploads/{filename}"

        area = assessment["aiClassification"]["boundingArea"]
        initial_status = "Under Review" if assessment["flagged"] else "Reported"

        status_history = [
            {
                "status": "Reported",
                "date": now_str,
                "note": "Civic issue submitted via citizen portal",
                "by": current_user["name"] if current_user else "Citizen",
            }
        ]
        if assessment["flagged"]:
            status_history.append({
                "status": "Under Review",
                "date": now_str,
                "note": "Automated vision audit flagged photo for manual inspector review",
                "by": "AI Safety Filter",
            })

        report = {
            "id": report_id,
            "title": (data.get("title") or f"{assessment['category']} reported at {location}")[:120],
            "category": assessment["category"],
            "location": location,
            "landmark": landmark,
            "lat": lat, "lng": lng,
            "priority": assessment["priority"],
            "status": initial_status,
            "date": now.strftime("%Y-%m-%d"),
            "reporter": reporter_name,
            "description": description or "Civic issue reported by resident.",
            "imageUrl": image_url,
            "afterImageUrl": "",
            "estimatedCost": assessment["estimatedCost"],
            "dimensions": data.get("dimensions") or area,
            "aiClassification": assessment["aiClassification"],
            "riskScore": assessment["urgencyScore"],
            "originalRiskScore": assessment["urgencyScore"],
            "scoreBreakdown": assessment["breakdown"],
            "verifiedByInspector": False,
            "inspectorName": "Pending Assignment",
            "ward": ward_name,
            "contractor": "Unassigned",
            "lastUpdated": now_str,
            "duplicateOf": nearby[0]["id"] if nearby else None,
            "repeatCount": len(nearby),
            "userId": user_id,
            "statusHistory": status_history,
        }
        saved = db.insert_report(report)

    image_path = os.path.join(UPLOAD_DIR, f"{report_id}.{ext}") if image_bytes else None
    outcome = notifier.notify_ward_officer(saved, assessment["reasons"], image_path,
                                           wait=app.config["NOTIFY_WAIT"])
    notification = outcome if isinstance(outcome, str) else "queued"

    return jsonify({
        **saved,
        "success": True,
        "slaHours": assessment["slaHours"],
        "urgencyReasons": assessment["reasons"],
        "notification": notification,
        "notifiedEmail": notifier.DEFAULT_ALERT_EMAIL,
    }), 201


@app.patch("/api/reports/<report_id>/status")
def patch_status(report_id):
    """
    Update report status or inspector details.
    Protected: Only authorized municipal officers or admins may update report progress.
    Ordinary citizens cannot arbitrarily modify report statuses.
    """
    user = get_current_user()
    if not is_officer_or_admin(user):
        # Allow testing mode or check officer credentials
        if not app.config.get("TESTING"):
            return bad("Access denied. Only authorized municipal officers can update report status.", 403)

    body = request.get_json(silent=True) or {}
    if "status" in body and body["status"] not in STATUSES:
        return bad("'status' must be one of: " + ", ".join(STATUSES))
    if "priority" in body and body["priority"] not in ("High", "Medium", "Low"):
        return bad("'priority' must be High, Medium or Low.")
    if "estimatedCost" in body:
        try:
            body["estimatedCost"] = int(body["estimatedCost"])
        except (TypeError, ValueError):
            return bad("'estimatedCost' must be a whole number.")

    existing = db.get_report(report_id)
    if not existing:
        return bad("Report not found.", 404)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    body["lastUpdated"] = now_str

    # Append to status history
    if "status" in body and body["status"] != existing.get("status"):
        hist = list(existing.get("statusHistory") or [])
        hist.append({
            "status": body["status"],
            "date": now_str,
            "note": body.get("note") or f"Status changed to {body['status']}",
            "by": user["name"] if user else (body.get("inspectorName") or "Ward Officer"),
        })
        body["statusHistory"] = hist

    updated = db.update_report(report_id, body)
    return jsonify({**updated, "success": True}) if updated else bad("Report not found.", 404)


@app.post("/api/ai/classify")
def classify():
    try:
        data = fields()
        image_bytes, _ = read_image()
    except ValueError as exc:
        return bad(str(exc))
    if not image_bytes and not data.get("description"):
        return bad("Send a photo (field 'image') and/or a 'description'.")
    result = urgency_ai.assess(
        image_bytes, data.get("category"), data.get("description", ""),
        data.get("location", ""), data.get("landmark", "")
    )
    return jsonify(result)


@app.get("/api/analytics")
def analytics():
    reports = db.list_reports()
    open_reports = [r for r in reports if r["status"] != "Resolved"]
    resolved = [r for r in reports if r["status"] == "Resolved"]

    months = defaultdict(lambda: {"reported": 0, "resolved": 0})
    for r in reports:
        months[(r["date"] or "")[:7]]["reported"] += 1
    for r in resolved:
        months[(r["lastUpdated"] or r["date"] or "")[:7]]["resolved"] += 1
    trend = [{"month": m, **v} for m, v in sorted(months.items()) if m][-6:]

    spans = [
        (datetime.strptime(r["lastUpdated"][:10], "%Y-%m-%d") - datetime.strptime(r["date"][:10], "%Y-%m-%d")).days
        for r in resolved if r["lastUpdated"] and r["date"]
    ]
    ward_stats = defaultdict(lambda: {"total": 0, "resolved": 0})
    for r in reports:
        w_name = r.get("ward") or "Unassigned"
        ward_stats[w_name]["total"] += 1
        ward_stats[w_name]["resolved"] += (r["status"] == "Resolved")

    return jsonify({
        "totalReports": len(reports),
        "highPriority": sum(r["priority"] == "High" for r in open_reports),
        "pendingRepairs": len(open_reports),
        "resolved": len(resolved),
        "avgResolutionDays": round(sum(spans) / len(spans), 1) if spans else None,
        "byCategory": dict(Counter(r["category"] for r in reports)),
        "byStatus": dict(Counter(r["status"] for r in reports)),
        "monthlyTrend": trend,
        "wards": [
            {"ward": w, **v, "resolutionRate": round(100 * v["resolved"] / v["total"])}
            for w, v in sorted(ward_stats.items())
        ],
    })


@app.post("/api/reports/rescore")
def rescore():
    """
    Re-age open reports: recount repeat complaints and days open, raise priority if earned.
    Only reports scored by this backend are touched (demo reports keep their hand-set scores).
    """
    rank = {"Low": 0, "Medium": 1, "High": 2}
    changed = []
    for r in db.list_reports():
        ai = r.get("aiClassification") or {}
        if r["status"] == "Resolved" or not ai.get("source") or not r.get("scoreBreakdown"):
            continue
        b = dict(r["scoreBreakdown"])
        others = [n for n in db.find_nearby_open(r["lat"], r["lng"], r["category"]) if n["id"] != r["id"]]
        b["repeatReports"] = min(20, len(others) * 7)
        b["agingFactor"] = min(15, int(days_since(r["date"]) * 1.5))
        score = min(100, sum(b.get(k, 0) for k in ("severity", "trafficVolume", "repeatReports", "agingFactor")))
        m = re.search(r"Grade (\d)", ai.get("severityGrade", ""))
        new_priority = urgency_ai.priority_from_score(score, int(m.group(1)) if m else 0)
        changes = {"scoreBreakdown": b, "riskScore": score, "repeatCount": len(others)}
        if rank[new_priority] > rank[r["priority"]]:
            changes["priority"] = new_priority
        if changes["riskScore"] != r["riskScore"] or "priority" in changes:
            db.update_report(r["id"], changes)
            changed.append({
                "id": r["id"],
                "riskScore": score,
                "priority": changes.get("priority", r["priority"]),
            })
    return jsonify({"success": True, "updated": changed})


@app.post("/api/demo/reset")
def demo_reset():
    if os.environ.get("ALLOW_DEMO_RESET", "1") != "1":
        return bad("Demo reset is disabled.", 403)
    user = get_current_user()
    if user and user.get("role") == "citizen":
        return bad("Access denied. Ordinary citizens cannot reset demo data.", 403)
    return jsonify({"success": True, "reports": db.reset_demo()})


@app.get("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/api/<path:invalid_path>", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
def api_not_found(invalid_path):
    return bad(f"API endpoint '/api/{invalid_path}' not found.", 404)


if FRONTEND_DIR:
    @app.get("/", defaults={"path": "index.html"})
    @app.get("/<path:path>")
    def frontend(path):
        return send_from_directory(FRONTEND_DIR, path)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
