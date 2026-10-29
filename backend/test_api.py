"""
SUDHARSARTHI - End-to-end Backend & Authentication Tests.

Run:  python test_api.py
Uses a throw-away temporary database/uploads/outbox, requires no external network or API key.
"""
import io
import json
import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp()
os.environ["SUDHARSARTHI_DB"] = os.path.join(_tmp, "test.db")
os.environ["UPLOAD_DIR"] = os.path.join(_tmp, "uploads")
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("SMTP_HOST", None)

import app as app_module  # noqa: E402
import database as db  # noqa: E402
import duhai  # noqa: E402
import notifier  # noqa: E402
import urgency_ai  # noqa: E402
from PIL import Image  # noqa: E402

notifier.OUTBOX = os.path.join(_tmp, "outbox")
app_module.app.config["NOTIFY_WAIT"] = True
app_module.app.config["TESTING"] = True


def fake_photo(color=(90, 90, 90)):
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    buf.seek(0)
    return buf


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code, self._payload = status, payload

    def json(self):
        return {"content": [{"type": "text", "text": self._payload}]}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.c = app_module.app.test_client()

    # --- Storage / Health / Config
    def test_01_health_and_seed(self):
        r = self.c.get("/api/health").get_json()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["reports"], 12)
        self.assertEqual(r["aiMode"], "rules-engine")
        self.assertIn("Ghaziabad", r["serviceArea"])

    def test_02_config_expanded_boundary(self):
        cfg = self.c.get("/api/config").get_json()
        self.assertIn("Ghaziabad", cfg["area"])
        self.assertEqual(len(cfg["bounds"]), 4)
        s, w, n, e = cfg["bounds"]
        self.assertLess(s, 28.700)   # South expanded below 28.700
        self.assertLess(w, 77.440)   # West expanded west of 77.440
        self.assertGreater(n, 28.760)  # North expanded above 28.760
        self.assertGreater(e, 77.510)  # East expanded east of 77.510
        # Original Duhai coordinates are fully included inside
        self.assertTrue(duhai.inside(28.7285, 77.4740))

    def test_03_list_and_filter(self):
        high = self.c.get("/api/reports?priority=High").get_json()
        self.assertTrue(high and all(x["priority"] == "High" for x in high))
        one = self.c.get("/api/reports/ns-2026-1042").get_json()
        self.assertEqual(one["category"], "Pothole")
        self.assertEqual(self.c.get("/api/reports/NS-0000").status_code, 404)

    # --- Citizen Registration, Login, Logout, Session & Auth Protection
    def test_04_citizen_registration_and_validation(self):
        # Missing or invalid fields
        r1 = self.c.post("/api/auth/register", json={
            "name": "A", "email": "invalid-email", "phone": "123", "password": "pass", "confirmPassword": "diff"
        })
        self.assertEqual(r1.status_code, 400)

        # Valid registration
        valid_payload = {
            "name": "Devansh Sharma",
            "email": "devansh.citizen@example.com",
            "phone": "9876543210",
            "password": "SecurePassword123",
            "confirmPassword": "SecurePassword123"
        }
        r2 = self.c.post("/api/auth/register", json=valid_payload)
        self.assertEqual(r2.status_code, 201)
        d2 = r2.get_json()
        self.assertTrue(d2["success"])
        self.assertEqual(d2["user"]["email"], "devansh.citizen@example.com")
        self.assertNotIn("password", d2["user"])
        self.assertNotIn("password_hash", d2["user"])
        self.assertTrue(d2["token"])

        # Duplicate email registration rejected
        r3 = self.c.post("/api/auth/register", json={
            **valid_payload, "phone": "9876543211"
        })
        self.assertEqual(r3.status_code, 409)
        self.assertIn("email", r3.get_json()["error"].lower())

        # Duplicate phone registration rejected
        r4 = self.c.post("/api/auth/register", json={
            **valid_payload, "email": "other@example.com"
        })
        self.assertEqual(r4.status_code, 409)
        self.assertIn("mobile", r4.get_json()["error"].lower())

    def test_05_citizen_login_and_me_profile(self):
        # Failed login with wrong password
        r_fail = self.c.post("/api/auth/login", json={
            "identifier": "devansh.citizen@example.com",
            "password": "WrongPassword!"
        })
        self.assertEqual(r_fail.status_code, 401)
        self.assertIn("Invalid", r_fail.get_json()["error"])

        # Successful login using email
        r_ok = self.c.post("/api/auth/login", json={
            "identifier": "devansh.citizen@example.com",
            "password": "SecurePassword123"
        })
        self.assertEqual(r_ok.status_code, 200)
        d_ok = r_ok.get_json()
        self.assertTrue(d_ok["success"])
        token = d_ok["token"]

        # Fetch current user using header
        me_resp = self.c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_resp.status_code, 200)
        self.assertEqual(me_resp.get_json()["user"]["name"], "Devansh Sharma")

        # Successful login using mobile number
        r_phone = self.c.post("/api/auth/login", json={
            "identifier": "9876543210",
            "password": "SecurePassword123"
        })
        self.assertEqual(r_phone.status_code, 200)

        # Logout
        r_logout = self.c.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r_logout.status_code, 200)

        # Old token is invalidated
        me_after = self.c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_after.status_code, 401)

    def test_06_authenticated_citizen_report_submission_and_isolation(self):
        # Register Citizen 1
        c1_token = self.c.post("/api/auth/register", json={
            "name": "Priya Verma",
            "email": "priya.verma@example.com",
            "phone": "9811122233",
            "password": "Password123",
            "confirmPassword": "Password123"
        }).get_json()["token"]

        # Register Citizen 2
        c2_token = self.c.post("/api/auth/register", json={
            "name": "Rahul Mehta",
            "email": "rahul.mehta@example.com",
            "phone": "9822233344",
            "password": "Password123",
            "confirmPassword": "Password123"
        }).get_json()["token"]

        # Citizen 1 submits a report in Raj Nagar (expanded area)
        rep_resp = self.c.post("/api/reports", json={
            "category": "Pothole",
            "lat": 28.6860,
            "lng": 77.4420,
            "location": "Near ALT Centre, Raj Nagar",
            "description": "Deep crater pothole 15 cm deep on main road near hospital, two-wheelers crashing"
        }, headers={"Authorization": f"Bearer {c1_token}"})
        self.assertEqual(rep_resp.status_code, 201)
        created_report = rep_resp.get_json()
        self.assertEqual(created_report["priority"], "High")

        # Citizen 1 dashboard sees their report
        c1_dash = self.c.get("/api/citizen/reports", headers={"Authorization": f"Bearer {c1_token}"}).get_json()
        self.assertEqual(c1_dash["stats"]["total"], 1)
        self.assertEqual(c1_dash["reports"][0]["id"], created_report["id"])

        # Citizen 2 dashboard DOES NOT see Citizen 1's report (strict data isolation)
        c2_dash = self.c.get("/api/citizen/reports", headers={"Authorization": f"Bearer {c2_token}"}).get_json()
        self.assertEqual(c2_dash["stats"]["total"], 0)
        self.assertEqual(len(c2_dash["reports"]), 0)

    # --- Boundary Validation (Nationwide India with Ghaziabad-Duhai Focus)
    def test_07_boundary_validation_inside_and_outside(self):
        # Inside original Duhai region -> Accepted
        r_duhai = self.c.post("/api/reports", json={
            "category": "Waterlogging", "lat": 28.7285, "lng": 77.4740,
            "location": "Duhai Station Road", "description": "Water pooled near depot"
        })
        self.assertEqual(r_duhai.status_code, 201)

        # Inside expanded Ghaziabad region (Indirapuram / Mohan Nagar / Raj Nagar) -> Accepted
        r_ghaziabad = self.c.post("/api/reports", json={
            "category": "Road Crack", "lat": 28.6400, "lng": 77.3700,
            "location": "Indirapuram Sector 4", "description": "Road crack along median"
        })
        self.assertEqual(r_ghaziabad.status_code, 201)

        # Across India (e.g. Lucknow, Mumbai, Bengaluru) -> Accepted
        r_lucknow = self.c.post("/api/reports", json={
            "category": "Pothole", "lat": 26.8467, "lng": 80.9462,  # Lucknow
            "location": "Hazratganj, Lucknow", "description": "Civic issue reported in state capital"
        })
        self.assertEqual(r_lucknow.status_code, 201)

        # Outside India boundary (e.g., London) -> Rejected
        r_out = self.c.post("/api/reports", json={
            "category": "Pothole", "lat": 51.5074, "lng": -0.1278,  # London
            "location": "Trafalgar Square, London", "description": "Outside supported national boundary"
        })
        self.assertEqual(r_out.status_code, 400)
        self.assertIn("outside", r_out.get_json()["error"].lower())

    # --- Creating reports + AI urgency + photo upload
    def test_08_create_urgent_report_with_photo(self):
        r = self.c.post("/api/reports", data={
            "category": "Pothole", "lat": "28.7285", "lng": "77.4740",
            "location": "Master Plan Road near City Hospital, Raj Nagar",
            "description": "Deep pothole, about 14 cm deep, two-wheelers fell here last night, no street light.",
            "image": (fake_photo(), "pothole.jpg")
        }, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201)
        d = r.get_json()
        self.assertEqual(d["priority"], "High")
        self.assertGreaterEqual(d["riskScore"], 60)
        self.assertEqual(d["aiClassification"]["source"], "rules-engine")
        self.assertTrue(any("hospital" in x for x in d["urgencyReasons"]))
        self.assertTrue(os.path.exists(os.path.join(notifier.OUTBOX, f"{d['id']}.txt")))

    def test_09_duplicate_detection(self):
        r = self.c.post("/api/reports", json={
            "category": "Pothole", "lat": 28.72853, "lng": 77.47403,
            "description": "Pothole on main road"
        })
        d = r.get_json()
        self.assertEqual(r.status_code, 201)
        self.assertIsNotNone(d["duplicateOf"])
        self.assertEqual(d["repeatCount"], 1)

    # --- Classify Endpoint
    def test_10_classify_endpoint(self):
        r = self.c.post("/api/ai/classify", data={
            "category": "Waterlogging", "description": "Road blocked, water 30 cm deep near school",
            "image": (fake_photo(), "w.jpg")
        }, content_type="multipart/form-data")
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(d["severity"], 4)

    # --- Status Patching Protection
    def test_11_status_patch(self):
        r = self.c.patch("/api/reports/NS-2026-1042/status", json={
            "status": "Resolved", "verifiedByInspector": True, "contractor": "Apex Roadways Ltd"
        })
        d = r.get_json()
        self.assertEqual(d["status"], "Resolved")
        self.assertTrue(d["verifiedByInspector"])
        self.assertEqual(self.c.patch("/api/reports/NS-2026-1042/status",
                                      json={"status": "Nonsense"}).status_code, 400)

    # --- Verification of Budget AI complete removal
    def test_12_budget_ai_completely_removed(self):
        """Verify that /api/budget/optimize is no longer routed or available."""
        r = self.c.post("/api/budget/optimize", json={"budget": 250000, "strategy": "balanced"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(hasattr(app_module, "knapsack"))
        self.assertFalse(hasattr(app_module, "optimize_budget"))

    # --- Analytics & Demo Reset
    def test_13_analytics(self):
        a = self.c.get("/api/analytics").get_json()
        self.assertEqual(a["totalReports"], len(self.c.get("/api/reports").get_json()))
        self.assertIn("Pothole", a["byCategory"])
        self.assertTrue(a["monthlyTrend"])

    def test_14_demo_reset(self):
        r = self.c.post("/api/demo/reset").get_json()
        self.assertEqual(r["reports"], 12)

    def test_15_login_rate_limiting_and_generic_errors(self):
        # Multiple failed logins should trigger rate limiting
        ip_header = {"X-Forwarded-For": "192.168.1.100"}
        for _ in range(5):
            res = self.c.post("/api/auth/login", json={
                "identifier": "test.ratelimit@example.com",
                "password": "WrongPassword1"
            }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.get_json()["error"], "Invalid email/mobile number or password.")

        # 6th attempt is rate limited (429)
        blocked = self.c.post("/api/auth/login", json={
            "identifier": "test.ratelimit@example.com",
            "password": "WrongPassword1"
        }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Too many failed login attempts", blocked.get_json()["error"])

    def test_16_unauthenticated_citizen_reports_rejected(self):
        # Accessing /api/citizen/reports without a valid token must return 401
        res = self.c.get("/api/citizen/reports")
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
