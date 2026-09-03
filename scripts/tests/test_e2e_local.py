#!/usr/bin/env python3
"""E2E Test Suite for directorpersonalcode.uk"""
import os, sys, tempfile, json, glob

STAFF = "local-e2e-passcode"
os.environ["STAFF_PASSCODE"] = STAFF
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "e2e.db")
for k in ("STRIPE_SECRET_KEY", "BREVO_API_KEY", "TRUSTID_API_KEY"):
    os.environ.pop(k, None)

# Add repository root and backend directory to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))

# Locate api_server.py dynamically across the repository
api_server_paths = glob.glob(os.path.join(REPO_ROOT, "**", "api_server.py"), recursive=True)
if api_server_paths:
    backend_dir = os.path.dirname(api_server_paths[0])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
else:
    sys.path.insert(0, REPO_ROOT)

from fastapi.testclient import TestClient
import api_server

# Explicitly initialize database tables before testing
for fn in ("init_db", "create_tables", "setup_db"):
    if hasattr(api_server, fn):
        try:
            getattr(api_server, fn)()
        except Exception as e:
            print(f"DB init warning ({fn}): {e}")

results = []

def check(name, got, want, detail=""):
    ok = got == want
    results.append((ok, name))
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}: got {got!r}, want {want!r}")
    if not ok and detail:
        print(f"         Detail: {detail}")

CASE = "IDV-2026-90000001"
payload = {
    "case_ref": CASE, "full_name": "E2E Test Applicant", "first_name": "E2E",
    "last_name": "Applicant", "email": "e2e-test@example.invalid",
    "residency": "uk", "fee_amount": 125.0, "route": "online",
    "role": "Director", "dob": "1985-04-12", "nationality": "British",
    "residence_country": "United Kingdom",
    "home_address": "1 Test Street, Testville, TE5 7XX", "address_since": "2019-06",
    "mobile": "+44 7000 000000", "company_name": "Test Company Ltd",
    "company_number": "08408126", "role_confirm": "yes",
    "sign_name": "E2E Test Applicant", "sign_date": "2026-08-25",
    "sign_ip": "203.0.113.9",
    "signature_data": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
}

# Run inside TestClient context manager to execute FastAPI startup handlers
with TestClient(api_server.app) as c:
    print("STEP 1 — applicant submits wizard")
    r = c.post("/api/applications", json=payload)
    check("record created (201)", r.status_code, 201, detail=r.text[:200])
    
    body = r.json() if r.status_code < 300 else {}
    token = body.get("access_token") or ""
    check("backend issued access token", bool(token and len(token) >= 16), True)

    print("\nSTEP 2 — token enforcement")
    check("payment-status with NO token", c.get(f"/api/applications/{CASE}/payment-status").status_code, 403)
    check("payment-status with WRONG token", c.get(f"/api/applications/{CASE}/payment-status", params={"token": "wrong"}).status_code, 403)
    check("payment-status with correct token", c.get(f"/api/applications/{CASE}/payment-status", params={"token": token}).status_code, 200)

    print("\nSTEP 3 — unknown case ref")
    check("unknown case ref", c.get("/api/applications/IDV-2026-00000000/payment-status", params={"token": token}).status_code, 404)

    print("\nSTEP 4 — mark submitted")
    r = c.post(f"/api/applications/{CASE}/submitted", params={"token": token})
    check("submitted accepted", r.status_code, 200, detail=r.text[:200])

    print("\nSTEP 5 — staff dashboard")
    hdr_name = getattr(api_server, 'STAFF_PASSCODE_HEADER', 'X-Staff-Passcode')
    r = c.get("/api/applications", headers={hdr_name: STAFF})
    check("staff listing authorised", r.status_code, 200, detail=r.text[:200])

passed = sum(1 for ok, _ in results if ok)
total = len(results)
print(f"\n{'='*56}\n{passed}/{total} checks passed")
sys.exit(0 if passed == total else 1)
