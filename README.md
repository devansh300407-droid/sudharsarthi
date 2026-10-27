# SUDHARSARTHI | Civic Issue & Road Safety Monitor

**SudharSarthi** is an intelligent civic issue reporting and municipal management platform upgraded for institute-level hackathon presentation. Citizens report road hazards (potholes, surface cracks, waterlogging, fallen trees, garbage dumps) with photos and precise GPS locations. An AI-powered urgency scoring engine classifies the hazard, detects duplicates, assigns priority (High, Medium, Low), dispatches alerts to municipal ward officers, and allows citizens to track resolution in real time.

---

## What's New in this Upgrade

1. **Complete Removal of Budget AI**:
   - Budget optimizer UI, controls, knapsack algorithms, and `/api/budget/optimize` endpoint have been completely removed.
   - Core AI urgency assessment, vision auditing, repeat report detection, and priority calculation remain 100% operational.

2. **Citizen Authentication & Personalized Dashboard**:
   - Real authentication system connected to Flask & SQLite.
   - Secure registration: Full Name, 10-digit Indian mobile number, normalized email, and password.
   - Secure password hashing using PBKDF2:SHA-256 (Werkzeug). Plaintext passwords are never stored or returned.
   - Citizen login via email or mobile number with brute-force rate limiting.
   - Persistent session management with secure HTTP-only cookies and Bearer tokens.
   - Personalized Citizen Dashboard: real-time report count, review/repair/resolved status tracking, and private report isolation.

3. **Expanded Service Area & Map Coverage**:
   - Service boundary expanded from a small Duhai box to the full **Ghaziabad & Duhai Metropolitan NCR Region** (~26 km × 26 km), covering Duhai, Raj Nagar, Kavi Nagar, Govindpuram, Sanjay Nagar, Mohan Nagar, Sahibabad, Indirapuram, Crossings Republik, and Muradnagar.
   - Configurable service boundary via environment variables in `backend/duhai.py`.
   - Browser GPS detection ("Use My Current Location") with boundary validation.
   - Click-to-pin location selection with live coordinate display.
   - Fast region navigation shortcuts.

4. **Modern Civic-Tech UI & Mobile-First Experience**:
   - Modern civic-tech design system with deep navy, emerald teal, and accessible priority badges.
   - Fully responsive design: tested for mobile screens (360px, 390px), tablets (768px), and desktops.
   - Touch targets >= 44×44px, 16px input font size to prevent iOS auto-zoom.
   - Dynamic photo capture with camera integration (`capture="environment"`).
   - Real-time AI vision pre-scan estimate before form submission.
   - Interactive submission success confirmation with copyable tracking ID.

---

## Project Structure

```
SUDHARSARTHI/
├── backend/
│   ├── app.py               # Flask REST API (Auth, Reports, Analytics, Health)
│   ├── database.py          # SQLite persistence, schema migrations, user & session store
│   ├── duhai.py             # Service boundary configuration (Ghaziabad & Duhai)
│   ├── urgency_ai.py        # Explainable AI urgency scoring (Claude vision + Heuristics)
│   ├── notifier.py          # Municipal ward officer email alerts & outbox logger
│   ├── wards.json           # Ward zones & officer contacts
│   ├── seed_reports.json    # Initial demonstration reports
│   ├── test_api.py          # Comprehensive test suite (16 tests)
│   ├── .env.example         # Environment template
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── index.html           # Modern responsive single-page civic-tech application
│   ├── vercel.json          # Vercel proxy & rewrites
│   └── assets/              # SVG logos and favicons
├── render.yaml              # Render deployment configuration
├── Procfile                 # Production web server command
├── requirements.txt         # Root dependency definition
└── README.md
```

---

## Running Locally on Windows

### Prerequisites
- Python 3.10+ installed.

### Step 1: Open PowerShell and navigate to the project directory
```powershell
cd "C:\Users\DEVANSH SHARMA\.gemini\antigravity\scratch\SUDHARSARTHI\backend"
```

### Step 2: Install dependencies
```powershell
pip install -r requirements.txt
```

### Step 3: Run automated tests
```powershell
python test_api.py
```
*(All 16 tests should pass with `OK`.)*

### Step 4: Start the application
```powershell
python app.py
```
Open your browser and navigate to:
**`http://localhost:5000`**

The Flask server automatically serves the frontend single-page application and the REST API simultaneously.

---

## Production Deployment Guide

### Option A: Render (Backend & Full-Stack)
1. Push this repository to GitHub.
2. In [Render Dashboard](https://dashboard.render.com), click **New +** -> **Web Service** and connect the repository.
3. Configure settings:
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn --chdir backend app:app --bind 0.0.0.0:$PORT`
4. Set Environment Variables:
   - `SESSION_SECRET`: A strong random string (e.g. `openssl rand -hex 32`)
   - `CORS_ORIGIN`: `*` (or your frontend domain)
   - `COOKIE_SECURE`: `1`
   - `ALERT_RECIPIENT_EMAIL`: your alert email address
   - `ANTHROPIC_API_KEY`: *(Optional)* Anthropic API key for Claude vision audit.
   - `SMTP_*`: *(Optional)* Gmail app password settings for live officer emails.

> [!NOTE]
> **Database Persistence on Render Free Tier:**
> Render's free tier uses an ephemeral filesystem that resets when the service spins down.
> For production data persistence across deploys and restarts:
> 1. Attach a **Render Persistent Disk** mounted at `/opt/render/project/src/backend/data` and set `SUDHARSARTHI_DB=/opt/render/project/src/backend/data/sudharsarthi.db`.
> 2. Or migrate the SQLite queries in `database.py` to a managed PostgreSQL database instance by setting a `DATABASE_URL`.

### Option B: Vercel (Frontend) + Render (Backend)
1. Import the repository into [Vercel](https://vercel.com).
2. Set Root Directory to `frontend`.
3. The included `vercel.json` automatically proxies `/api/*` and `/uploads/*` to the Render backend URL.
4. If your Render service URL changes, update `vercel.json` or pass `?backend=https://your-service.onrender.com` in the URL.

---

## API Endpoints Reference

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `GET` | `/api/health` | Health status, active AI mode, email mode | No |
| `GET` | `/api/config` | Service bounds, map center, categories, wards | No |
| `POST` | `/api/auth/register` | Register new citizen account | No |
| `POST` | `/api/auth/login` | Login via email/phone + password | No |
| `POST` | `/api/auth/logout` | Invalidate session token & clear cookie | Yes |
| `GET` | `/api/auth/me` | Current authenticated citizen profile | Yes |
| `GET` | `/api/citizen/reports`| Authenticated citizen's own reports & stats | Yes |
| `GET` | `/api/reports` | List civic reports with filters | No |
| `GET` | `/api/reports/<id>` | Fetch single report details | No |
| `POST` | `/api/reports` | Submit new report (with photo + GPS) | Optional (auto-associates if logged in) |
| `PATCH` | `/api/reports/<id>/status` | Update issue status / repair verification | Officer / Admin Only |
| `POST` | `/api/ai/classify` | Vision & rule pre-scan on photo/description | No |
| `GET` | `/api/analytics` | City-wide metrics, categories & trends | No |
| `POST` | `/api/reports/rescore` | Re-age open reports & recount repeat complaints | Officer / System |
| `POST` | `/api/demo/reset` | Reset sample demo reports | Admin / Officer Only |
