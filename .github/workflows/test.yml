#!/usr/bin/env python3
"""Full applicant journey against the REAL backend code, on a throwaway DB.

Drives the same sequence of calls that js/apply.js makes in a real browser, so
the whole data path is exercised without touching production and without any
payment. External services (Stripe/Brevo/TrustID) are deliberately left
unconfigured here, so this also proves the code degrades gracefully rather than
throwing 500s when a third party is unavailable.
"""
import os, sys, tempfile, json

STAFF = "local-e2e-passcode"
os.environ["STAFF_PASSCODE"] = STAFF
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "e2e.db")
for k in ("STRIPE_SECRET_KEY", "BREVO_API_KEY", "TRUSTID_API_KEY"):
    os.environ.pop(k, None)

# Dynamically locate the tah-verify-site/backend directory relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "tah-verify-site", "backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient  # noqa: E402
import api_server  # noqa: E402

HDR = {api_server.STAFF_PASSCODE_HEADER: STAFF}
c = TestClient(api_server.app)
results = []


def check(name, got, want):
    ok = got == want
    results.append((ok, name))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r}, want {want!r}")


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

print("STEP 1 — applicant submits the wizard: POST /api/applications")
r = c.post("/api/applications", json=payload)
check("record created (201)", r.status_code, 201)
body = r.json() if r.status_code < 300 else {}
token = body.get("access_token") or ""
check("backend issued a per-case access token", bool(token and len(token) >= 16), True)

print("\nSTEP 2 — that token is genuinely enforced (case refs alone must not grant access)")
check("payment-status with NO token", c.get(f"/api/applications/{CASE}/payment-status").status_code, 403)
check("payment-status with WRONG token", c.get(f"/api/applications/{CASE}/payment-status", params={"token": "wrong"}).status_code, 403)
check("payment-status with correct token", c.get(f"/api/applications/{CASE}/payment-status", params={"token": token}).status_code, 200)

print("\nSTEP 3 — an unknown case reference cannot be enumerated")
check("unknown case ref", c.get("/api/applications/IDV-2026-00000000/payment-status", params={"token": token}).status_code, 404)

print("\nSTEP 4 — mark submitted + staff notification (Brevo unconfigured here)")
r = c.post(f"/api/applications/{CASE}/submitted", params={"token": token})
check("submitted accepted, no 500 despite no email provider", r.status_code, 200)
check("submitted rejects a wrong token", c.post(f"/api/applications/{CASE}/submitted", params={"token": 'wrong'}).status_code, 403)

print("\nSTEP 5 — payment link request with NO Stripe key must fail cleanly, not 500")
r = c.post(f"/api/applications/{CASE}/payment", params={"token": token}, json={"origin": "https://directorpersonalcode.uk"})
check("payment request degrades gracefully (not a 500)", r.status_code != 500, True)
print(f"         -> status {r.status_code}, detail: {str(r.json().get('detail'))[:90] if r.headers.get('content-type','').startswith('application/json') else 'n/a'}")
check("no payment recorded as paid without a real payment",
      c.get(f"/api/applications/{CASE}/payment-status", params={"token": token}).json().get("payment_status") != "paid", True)

print("\nSTEP 6 — staff dashboard sees the case, with ALL the collected detail")
r = c.get("/api/applications", headers=HDR)
check("staff listing authorised via header", r.status_code, 200)
rows = r.json() if r.status_code == 200 else []
rec = next((x for x in rows if x.get("case_ref") == CASE), None)
check("our case appears in the staff listing", rec is not None, True)
if rec:
    missing = [f for f in ("full_name", "email", "residency", "fee_amount", "role", "dob",
                           "nationality", "home_address", "mobile", "company_name",
                           "company_number", "sign_name", "sign_ip") if not rec.get(f)]
    check("every collected field persisted (none silently dropped)", missing, [])
    check("signature image stored", bool(rec.get("signature_data")), True)

print("\nSTEP 7 — staff record the Identity Verification Statement outcome")
r = c.post(f"/api/applications/{CASE}/verification", headers=HDR, json={"status": "verified"})
check("verification update accepted", r.status_code in (200, 201), True)
check("verification update refused without staff auth",
      c.post(f"/api/applications/{CASE}/verification", json={"status": "verified"}).status_code, 403)

print("\nSTEP 8 — staff deletion works and is protected (LOCAL DB ONLY)")
check("delete refused without staff auth", c.delete(f"/api/applications/{CASE}").status_code, 403)
check("delete accepted with staff auth", c.delete(f"/api/applications/{CASE}", headers=HDR).status_code, 200)
check("record really gone afterwards", c.get(f"/api/applications/{CASE}/payment-status", params={"token": token}).status_code, 404)

print("\nSTEP 9 — webhooks reject unsigned/forged calls")
check("Stripe webhook rejects an unsigned payload",
      c.post("/api/webhooks/stripe", json={"type": "checkout.session.completed"}).status_code >= 400, True)

passed = sum(1 for ok, _ in results if ok)
print(f"\n{'='*56}\n{passed}/{len(results)} checks passed")
for ok, n in results:
    if not ok:
        print("  FAILED:", n)
sys.exit(0 if passed == len(results) else 1)
