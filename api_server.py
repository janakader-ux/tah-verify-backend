#!/usr/bin/env python3
"""
api_server.py — TAH Verify backend, runs on port 8000 inside the sandbox.

Handles:
  - Storing wizard applications (case reference, applicant details, fee, route)
  - Storing in-person appointment requests (office / date / time preference)
  - Creating SumUp Hosted Checkout payment links + polling payment status
  - A scaffolded TrustID verification module (NOT live yet — see trustid_client.py)
  - A lightweight passcode-protected staff view listing applications and their
    payment / verification status, so staff know when to manually:
      1) trigger a TrustID Guest Link (until API creds are confirmed) for the
         online route, or confirm an appointment slot for the in-person route, and
      2) enter the verified director/PSC details into the Companies House /
         GOV.UK portal (Companies House has no public API for this step).

Note on email notifications: this backend sends every "application
submitted" / "payment received" / "appointment requested" notification
email itself, server-side, via Brevo's transactional email HTTPS API
(https://api.brevo.com/v3/smtp/email). This replaced an earlier client-side-
only FormSubmit.co integration that silently dropped every submission
whenever its one-time "Activate Form" confirmation link had not been
clicked (or expired) — a single point of failure with no server-side
fallback and no visibility into failures. Sending from the backend means
the notification fires the moment the event happens in our own database,
regardless of the applicant's browser, ad blockers, or third-party
confirmation state.

Env vars used:
  STRIPE_SECRET_KEY    -> Stripe secret key, sent as a Bearer token. Stripe is
                          the PRIMARY payment provider when this is set.
  SUMUP_API_KEY        -> SumUp API key/token, sent as a Bearer token (plain
                          env var — safe to pass directly to publish_website).
                          Used as a FALLBACK payment provider when
                          STRIPE_SECRET_KEY is not configured.
  SUMUP_MERCHANT_CODE  -> SumUp merchant code (not secret, plain env var)
  STAFF_PASSCODE       -> shared passcode for the staff view
  BREVO_API_KEY        -> Brevo transactional email API key (sent as the
                          `api-key` header)
  BREVO_SENDER_EMAIL   -> verified "single sender" address in Brevo
                          (defaults to info@taxandaccountinghub.com)
  NOTIFICATION_EMAIL   -> destination inbox for all staff notifications
                          (defaults to info@taxandaccountinghub.com)

If SUMUP_API_KEY is not set, the credential-proxy variables
(CUSTOM_CRED_API_SUMUP_COM_URL / CUSTOM_CRED_API_SUMUP_COM_TOKEN) are used as
a fallback, so the custom-credentials flow also works during development.
Either way SumUp is always called with a Bearer token, since that's what
SumUp's real API expects. Likewise BREVO_API_KEY falls back to
CUSTOM_CRED_API_BREVO_COM_TOKEN when unset, and STRIPE_SECRET_KEY falls back
to CUSTOM_CRED_API_STRIPE_COM_TOKEN when unset.

Payment provider selection: /api/applications/{case_ref}/payment picks
Stripe whenever a Stripe key is configured (the primary path going forward),
and only falls back to SumUp if Stripe isn't configured. Both providers are
normalized to the same {checkout_id, hosted_checkout_url} response shape, so
the frontend and the payment-status polling loop work identically either
way. Each application row remembers which provider it used
(`payment_provider` column) so status polling calls the right API later.

Note on SumUp's OWN payment-receipt email: SumUp may also send its own
transaction receipt to the merchant account's registered notification
address. That is controlled entirely by SumUp's own merchant dashboard
settings (Settings -> Notifications), not by this codebase — if that email
specifically is missing, check the SumUp dashboard's notification email
address and preferences. The "Payment received" email sent by this backend
(below) is independent of that and does not rely on SumUp's settings.

Note: SumUp has no separate sandbox/test mode for this checkout flow — every
checkout created here is a REAL, live payment request against the connected
merchant account.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from hmac import compare_digest
from contextlib import asynccontextmanager
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import trustid_client

logger = logging.getLogger("tah_verify")
logging.basicConfig(level=logging.INFO)

# DB_PATH can be overridden via env var to point at a persistent disk mount
# (e.g. Render persistent disks live outside the app's ephemeral filesystem,
# so set DB_PATH=/var/data/data.db once a disk is attached at /var/data).
DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")
)

# Staff access must fail closed. Set STAFF_PASSCODE as a deployment secret;
# there is intentionally no source-controlled fallback.
STAFF_PASSCODE = os.environ.get("STAFF_PASSCODE", "")

NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "info@taxandaccountinghub.com")
BREVO_API_HOST = "https://api.brevo.com"
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "") or os.environ.get("CUSTOM_CRED_API_BREVO_COM_TOKEN", "")
# Prefer the plain, publish-safe env var; fall back to the dev-sandbox
# credential-proxy host (agent_pass_through) only if the plain var is unset —
# proxy-issued tokens (agp_...) only work when the request actually goes
# through the proxy URL, not directly to api.brevo.com.
if not os.environ.get("BREVO_API_KEY") and os.environ.get("CUSTOM_CRED_API_BREVO_COM_URL"):
    BREVO_API_HOST = os.environ.get("CUSTOM_CRED_API_BREVO_COM_URL", "").rstrip("/")
BREVO_API_URL = f"{BREVO_API_HOST}/v3/smtp/email"
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "info@taxandaccountinghub.com")
BREVO_SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "Verify Your ID for Companies House — website")

STRIPE_BASE_URL = "https://api.stripe.com"
STRIPE_TOKEN = os.environ.get("STRIPE_SECRET_KEY", "") or os.environ.get("CUSTOM_CRED_API_STRIPE_COM_TOKEN", "")
# Same proxy-routing fix as SumUp/Brevo: a proxy-issued token (agp_...) must be
# sent to the credential proxy's own URL, not directly to api.stripe.com.
if not os.environ.get("STRIPE_SECRET_KEY") and os.environ.get("CUSTOM_CRED_API_STRIPE_COM_URL"):
    STRIPE_BASE_URL = os.environ.get("CUSTOM_CRED_API_STRIPE_COM_URL", "").rstrip("/")

SUMUP_MERCHANT_CODE = os.environ.get("SUMUP_MERCHANT_CODE", "")
SUMUP_BASE_URL = "https://api.sumup.com"
# Prefer the plain, publish-safe env var; fall back to the dev-sandbox
# credential-proxy vars (and its own base URL) only if the plain var is unset.
SUMUP_TOKEN = os.environ.get("SUMUP_API_KEY", "") or os.environ.get("CUSTOM_CRED_API_SUMUP_COM_TOKEN", "")
if not os.environ.get("SUMUP_API_KEY") and os.environ.get("CUSTOM_CRED_API_SUMUP_COM_URL"):
    SUMUP_BASE_URL = os.environ.get("CUSTOM_CRED_API_SUMUP_COM_URL", "").rstrip("/")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    """
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_ref TEXT UNIQUE NOT NULL,
        full_name TEXT,
        first_name TEXT,
        last_name TEXT,
        email TEXT,
        residency TEXT,
        fee_amount REAL,
        route TEXT,
        appointment_type TEXT,
        appointment_office TEXT,
        appointment_date TEXT,
        appointment_time_pref TEXT,
        payment_status TEXT DEFAULT 'pending',
        payment_checkout_id TEXT,
        payment_url TEXT,
        verification_status TEXT DEFAULT 'not_started',
        verification_notes TEXT,
        submitted INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
)
db.commit()

# Lightweight forward-compatible migration for databases created before the
# appointment/route/first-last-name columns existed.
_existing_cols = {row[1] for row in db.execute("PRAGMA table_info(applications)")}
for _col, _decl in [
    ("first_name", "TEXT"),
    ("last_name", "TEXT"),
    ("route", "TEXT"),
    ("appointment_type", "TEXT"),
    ("appointment_office", "TEXT"),
    ("appointment_date", "TEXT"),
    ("appointment_time_pref", "TEXT"),
    ("payment_provider", "TEXT"),
    ("access_token", "TEXT"),
]:
    if _col not in _existing_cols:
        db.execute(f"ALTER TABLE applications ADD COLUMN {_col} {_decl}")
db.commit()


@asynccontextmanager
async def lifespan(app):
    yield
    db.close()


app = FastAPI(lifespan=lifespan)
# CORS was previously wide open (allow_origins=["*"]) alongside unauthenticated
# case-ref-scoped mutation endpoints below — combined, that let any website
# make cross-origin requests against this API on a visitor's behalf. Restrict
# to this site's own origins (the published pplx.app subdomain and the
# eventual custom domain) via ALLOWED_ORIGIN_REGEX, configurable per
# deployment; falls back to a safe default covering both if unset.
ALLOWED_ORIGIN_REGEX = os.environ.get(
    "ALLOWED_ORIGIN_REGEX",
    r"^https://([a-zA-Z0-9-]+\.)?(pplx\.app|taxandaccountinghub\.com)$|^http://localhost(:\d+)?$",
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_staff_passcode(passcode: str) -> None:
    """Fail closed when staff access has not been configured on the server."""
    if not STAFF_PASSCODE:
        logger.error("Staff-only endpoint called while STAFF_PASSCODE is not configured")
        raise HTTPException(503, "Staff access is not configured")
    if not compare_digest(passcode, STAFF_PASSCODE):
        raise HTTPException(403, "Invalid passcode")


class ApplicationIn(BaseModel):
    case_ref: str
    full_name: str
    email: str
    residency: str  # 'uk' | 'overseas'
    fee_amount: float
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    route: Optional[str] = None  # 'online' | 'in-person'
    appointment_type: Optional[str] = None  # 'in-person' | 'video-call'
    appointment_office: Optional[str] = None  # 'london' | 'bedford'
    appointment_date: Optional[str] = None
    appointment_time_pref: Optional[str] = None


class VerificationTrigger(BaseModel):
    case_ref: str


def row_to_dict(row, columns):
    return dict(zip(columns, row))


def get_application(case_ref: str):
    cur = db.execute("SELECT * FROM applications WHERE case_ref = ?", [case_ref])
    row = cur.fetchone()
    if not row:
        return None
    columns = [d[0] for d in cur.description]
    return row_to_dict(row, columns)


def require_case_token(record: dict, token: Optional[str]) -> None:
    """Case references are short and guessable (a 4-digit timestamp suffix),
    so relying on the reference alone to authorise reads/writes on someone
    else's application is not safe on a public internet-facing deployment.
    Every application gets a random access_token at creation, handed back to
    the applicant's own browser once; that token — not the case_ref — is now
    required to submit, request an appointment, create a payment link, or
    poll payment status for that specific application.
    """
    stored = record.get("access_token") or ""
    if not stored or not token or not compare_digest(token, stored):
        raise HTTPException(403, "Invalid or missing access token for this case reference")


def send_notification_email(subject: str, fields: dict, intro: str = "") -> bool:
    """Send a staff notification email via Brevo's transactional email API.

    Best-effort: never raises. Returns True on confirmed send, False
    otherwise (and logs the reason) so callers can proceed regardless —
    the database record is always the source of truth even if the email
    fails to send.
    """
    if not BREVO_API_KEY:
        logger.warning("Notification email skipped (BREVO_API_KEY not configured): %s", subject)
        return False
    rows_html = "".join(
        f"<tr><td style='padding:4px 10px;color:#666;font-family:sans-serif;font-size:13px;"
        f"vertical-align:top;white-space:nowrap;'>{k}</td>"
        f"<td style='padding:4px 10px;font-family:sans-serif;font-size:13px;'>{v}</td></tr>"
        for k, v in fields.items()
    )
    html = (
        "<div style='font-family:sans-serif;font-size:14px;color:#111;'>"
        + (f"<p>{intro}</p>" if intro else "")
        + f"<table style='border-collapse:collapse;margin-top:8px;'>{rows_html}</table></div>"
    )
    payload = {
        "sender": {"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
        "to": [{"email": NOTIFICATION_EMAIL}],
        "subject": subject,
        "htmlContent": html,
    }
    try:
        resp = requests.post(
            BREVO_API_URL,
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code >= 300:
            logger.error("Brevo email send failed (%s): %s", resp.status_code, resp.text[:500])
            return False
        logger.info("Notification email sent: %s", subject)
        return True
    except Exception as exc:  # noqa: BLE001 — notification failures must never break the request
        logger.exception("Notification email raised an exception: %s", exc)
        return False


@app.get("/health")
def health():
    return {
        "status": "ok",
        "stripe_configured": bool(STRIPE_TOKEN),
        "sumup_configured": bool(SUMUP_BASE_URL and SUMUP_TOKEN and SUMUP_MERCHANT_CODE),
        "active_payment_provider": "stripe" if STRIPE_TOKEN else ("sumup" if (SUMUP_TOKEN and SUMUP_MERCHANT_CODE) else None),
        "trustid_configured": trustid_client.is_configured(),
        "email_notifications_configured": bool(BREVO_API_KEY),
    }


@app.post("/api/diagnostics/send-test-email")
def send_test_email(passcode: str = Query(...)):
    """Staff-only: fire a real test notification email on demand, so the
    Brevo integration can be verified from the staff dashboard without
    needing a full test application + payment."""
    require_staff_passcode(passcode)
    if not BREVO_API_KEY:
        raise HTTPException(503, "BREVO_API_KEY is not configured on the server yet.")
    ok = send_notification_email(
        "Verify Your ID for Companies House — test notification email",
        {"Sent at": time.strftime("%Y-%m-%d %H:%M:%S"), "Sender": BREVO_SENDER_EMAIL, "Recipient": NOTIFICATION_EMAIL},
        intro="This is a diagnostic test email triggered from the staff dashboard to confirm email notifications are working.",
    )
    if not ok:
        raise HTTPException(502, "Brevo rejected the test email — check BREVO_API_KEY and the sender's verification status in the Brevo dashboard.")
    return {"sent": True}


@app.post("/api/diagnostics/trustid-test")
def trustid_test(passcode: str = Query(...)):
    """Staff-only: check TrustID connectivity on demand.

    Always runs the unauthenticated testConnection ping. If TRUSTID_SERVER /
    TRUSTID_USERNAME / TRUSTID_PASSWORD / TRUSTID_API_KEY are all set on this
    server's environment, also attempts a real login handshake so staff can
    confirm the credentials themselves are valid — without creating any
    Guest Link or sending any email."""
    require_staff_passcode(passcode)
    result = {
        "trustid_configured": trustid_client.is_configured(),
        "server": trustid_client.TRUSTID_SERVER or None,
        "connection": trustid_client.test_connection(),
    }
    if trustid_client.is_configured():
        try:
            login_data = trustid_client._login()
            result["login"] = {
                "success": bool(login_data.get("Success")),
                "message": login_data.get("Message"),
            }
        except Exception as exc:
            result["login"] = {"success": False, "message": str(exc)}
    else:
        result["login"] = {
            "success": False,
            "message": "TRUSTID_USERNAME / TRUSTID_PASSWORD / TRUSTID_API_KEY not set on the server yet.",
        }
    return result


@app.get("/api/config")
def config():
    """Small shared config surface so the frontend and staff dashboard don't
    hardcode office addresses / portal links in two places."""
    return {
        "trustid_guest_link_portal_url": trustid_client.TRUSTID_PORTAL_GUEST_LINK_URL,
        "offices": {
            "london": {
                "name": "London office",
                "address": "Hallings Wharf Studios, 1A Cam Road, London, E15 2SY",
            },
            "bedford": {
                "name": "Bedford office",
                "address": "11 Holbeach Avenue, Shortstown, Bedford, MK42 0EG",
            },
        },
    }


@app.post("/api/applications", status_code=201)
def create_application(app_in: ApplicationIn):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    existing = get_application(app_in.case_ref)
    if existing:
        db.execute(
            """UPDATE applications SET full_name=?, first_name=?, last_name=?, email=?, residency=?,
               fee_amount=?, route=?, appointment_type=?, appointment_office=?, appointment_date=?,
               appointment_time_pref=?, updated_at=? WHERE case_ref=?""",
            [
                app_in.full_name, app_in.first_name, app_in.last_name, app_in.email, app_in.residency,
                app_in.fee_amount, app_in.route, app_in.appointment_type, app_in.appointment_office,
                app_in.appointment_date, app_in.appointment_time_pref, now, app_in.case_ref,
            ],
        )
    else:
        db.execute(
            """INSERT INTO applications (case_ref, full_name, first_name, last_name, email, residency,
               fee_amount, route, appointment_type, appointment_office, appointment_date,
               appointment_time_pref, updated_at, access_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                app_in.case_ref, app_in.full_name, app_in.first_name, app_in.last_name, app_in.email,
                app_in.residency, app_in.fee_amount, app_in.route, app_in.appointment_type,
                app_in.appointment_office, app_in.appointment_date, app_in.appointment_time_pref, now,
                secrets.token_urlsafe(24),
            ],
        )
    db.commit()
    return get_application(app_in.case_ref)


@app.post("/api/applications/{case_ref}/submitted")
def mark_submitted(case_ref: str, token: str = Query(...)):
    record = get_application(case_ref)
    if not record:
        raise HTTPException(404, "Application not found")
    require_case_token(record, token)
    db.execute(
        "UPDATE applications SET submitted=1, updated_at=? WHERE case_ref=?",
        [time.strftime("%Y-%m-%d %H:%M:%S"), case_ref],
    )
    db.commit()
    updated = get_application(case_ref)
    send_notification_email(
        f"New ACSP ID verification application — Case {case_ref}",
        {
            "Case reference": case_ref,
            "Applicant": updated.get("full_name") or "",
            "Email": updated.get("email") or "",
            "Company": updated.get("route") or "",
            "Fee": f"£{updated.get('fee_amount')}" if updated.get("fee_amount") else "",
            "Verification route": updated.get("route") or "",
        },
        intro="A new ACSP identity verification application has been submitted and the engagement letter signed. Payment is next.",
    )
    return updated


class AppointmentIn(BaseModel):
    appointment_office: str  # 'london' | 'bedford'
    appointment_date: Optional[str] = None
    appointment_time_pref: Optional[str] = None


@app.post("/api/applications/{case_ref}/appointment")
def request_appointment(case_ref: str, body: AppointmentIn, token: str = Query(...)):
    record = get_application(case_ref)
    if not record:
        raise HTTPException(404, "Application not found")
    require_case_token(record, token)
    db.execute(
        """UPDATE applications SET appointment_type='in-person', appointment_office=?,
           appointment_date=?, appointment_time_pref=?, updated_at=? WHERE case_ref=?""",
        [body.appointment_office, body.appointment_date, body.appointment_time_pref,
         time.strftime("%Y-%m-%d %H:%M:%S"), case_ref],
    )
    db.commit()
    updated = get_application(case_ref)
    office_names = {"london": "London — Stratford", "bedford": "Bedford"}
    send_notification_email(
        f"In-person appointment requested — Case {case_ref}",
        {
            "Case reference": case_ref,
            "Applicant": updated.get("full_name") or "",
            "Email": updated.get("email") or "",
            "Office": office_names.get(body.appointment_office, body.appointment_office),
            "Preferred date": body.appointment_date or "",
            "Preferred time": body.appointment_time_pref or "",
        },
        intro="The applicant has requested an in-person ID verification appointment. Please confirm the exact slot with them.",
    )
    return updated


def sumup_request(method: str, path: str, json_body: Optional[dict] = None):
    if not (SUMUP_BASE_URL and SUMUP_TOKEN):
        raise HTTPException(503, "SumUp is not configured on the server yet.")
    url = f"{SUMUP_BASE_URL}{path}"
    # SumUp's real API always expects a Bearer token, whether SUMUP_TOKEN came
    # from the plain SUMUP_API_KEY env var or from the custom-credentials
    # proxy's CUSTOM_CRED_API_SUMUP_COM_TOKEN env var.
    headers = {"Authorization": f"Bearer {SUMUP_TOKEN}", "Content-Type": "application/json"}
    resp = requests.request(method, url, headers=headers, json=json_body, timeout=20)
    if resp.status_code >= 400:
        raise HTTPException(502, f"SumUp error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def stripe_request(method: str, path: str, form_body: Optional[dict] = None):
    if not STRIPE_TOKEN:
        raise HTTPException(503, "Stripe is not configured on the server yet.")
    url = f"{STRIPE_BASE_URL}{path}"
    # Stripe accepts the secret key as a Bearer token (equivalent to HTTP
    # Basic auth with the key as username), and its API is form-urlencoded
    # with bracket notation for nested params, not JSON.
    headers = {"Authorization": f"Bearer {STRIPE_TOKEN}"}
    resp = requests.request(method, url, headers=headers, data=form_body, timeout=20)
    if resp.status_code >= 400:
        raise HTTPException(502, f"Stripe error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def create_stripe_checkout(case_ref: str, record: dict, redirect_url: Optional[str], origin: str):
    unit_amount = int(round(float(record["fee_amount"]) * 100))
    payload = {
        "mode": "payment",
        "client_reference_id": case_ref,
        "success_url": (redirect_url or f"{origin}/payment-complete.html") + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": f"{origin}/apply.html",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "gbp",
        "line_items[0][price_data][unit_amount]": str(unit_amount),
        "line_items[0][price_data][product_data][name]": f"Verify My ID ACSP UK [TAH Verify] — ACSP identity verification ({case_ref})",
        "metadata[case_ref]": case_ref,
    }
    if record.get("email"):
        payload["customer_email"] = record["email"]
    data = stripe_request("POST", "/v1/checkout/sessions", payload)
    return data.get("id"), data.get("url")


@app.post("/api/applications/{case_ref}/payment")
def create_payment(case_ref: str, request_body: dict = None, token: str = Query(...)):
    record = get_application(case_ref)
    if not record:
        raise HTTPException(404, "Application not found")
    require_case_token(record, token)

    body = request_body or {}
    origin = body.get("origin", "").rstrip("/")
    redirect_url = f"{origin}/payment-complete.html" if origin else None

    if STRIPE_TOKEN:
        provider = "stripe"
        checkout_id, hosted_url = create_stripe_checkout(case_ref, record, redirect_url, origin)
    elif SUMUP_MERCHANT_CODE and SUMUP_TOKEN:
        provider = "sumup"
        payload = {
            "checkout_reference": f"{case_ref}-{uuid.uuid4().hex[:8]}",
            "amount": record["fee_amount"],
            "currency": "GBP",
            "merchant_code": SUMUP_MERCHANT_CODE,
            "description": f"Verify My ID ACSP UK [TAH Verify] — ACSP identity verification ({case_ref})",
            "hosted_checkout": {"enabled": True},
        }
        if redirect_url:
            payload["redirect_url"] = redirect_url
        data = sumup_request("POST", "/v0.1/checkouts", payload)
        checkout_id = data.get("id")
        hosted_url = data.get("hosted_checkout_url")
    else:
        raise HTTPException(503, "No payment provider (Stripe or SumUp) is configured on the server yet.")

    db.execute(
        "UPDATE applications SET payment_checkout_id=?, payment_url=?, payment_status='pending', payment_provider=?, updated_at=? WHERE case_ref=?",
        [checkout_id, hosted_url, provider, time.strftime("%Y-%m-%d %H:%M:%S"), case_ref],
    )
    db.commit()
    return {"checkout_id": checkout_id, "hosted_checkout_url": hosted_url, "provider": provider}


def _refresh_payment_status(case_ref: str):
    """Look up the authoritative payment status directly from the payment
    provider's own API (never from a caller-supplied value) and persist it.
    Called both from the public, token-checked endpoint below and from the
    webhook handlers — webhooks only use it to trigger a re-check keyed off a
    checkout_id already stored in our own database, they never let the
    incoming webhook body set the status directly.
    """
    record = get_application(case_ref)
    if not record:
        raise HTTPException(404, "Application not found")
    if not record["payment_checkout_id"]:
        return {"payment_status": record["payment_status"]}

    provider = record.get("payment_provider") or ("stripe" if STRIPE_TOKEN else "sumup")

    if provider == "stripe":
        data = stripe_request("GET", f"/v1/checkout/sessions/{record['payment_checkout_id']}")
        stripe_payment_status = data.get("payment_status", "unpaid")
        session_status = data.get("status", "open")
        if stripe_payment_status == "paid" or stripe_payment_status == "no_payment_required":
            mapped = "paid"
        elif session_status == "expired":
            mapped = "expired"
        else:
            mapped = "pending"
        status = f"{session_status}/{stripe_payment_status}"
    else:
        data = sumup_request("GET", f"/v0.1/checkouts/{record['payment_checkout_id']}")
        status = data.get("status", "PENDING")
        mapped = {"PAID": "paid", "PENDING": "pending", "FAILED": "failed", "EXPIRED": "expired"}.get(status, "pending")

    was_paid_already = record["payment_status"] == "paid"

    db.execute(
        "UPDATE applications SET payment_status=?, updated_at=? WHERE case_ref=?",
        [mapped, time.strftime("%Y-%m-%d %H:%M:%S"), case_ref],
    )
    db.commit()

    if mapped == "paid" and not was_paid_already:
        # First time we've observed this checkout as paid — fire our own
        # reliable notification. This is independent of whatever the payment
        # provider's own merchant-account receipt email does or doesn't do.
        send_notification_email(
            f"Payment received — Case {case_ref}",
            {
                "Case reference": case_ref,
                "Applicant": record.get("full_name") or "",
                "Email": record.get("email") or "",
                "Fee": f"£{record.get('fee_amount')}" if record.get("fee_amount") else "",
                "Verification route": record.get("route") or "",
            },
            intro=(
                "Payment has been received. Please initiate the TrustID guest link for this applicant."
                if record.get("route") == "online"
                else "Payment has been received. The applicant will request an in-person appointment next."
            ),
        )

    return {"payment_status": mapped, "raw_status": status}


@app.get("/api/applications/{case_ref}/payment-status")
def payment_status(case_ref: str, token: str = Query(...)):
    record = get_application(case_ref)
    if not record:
        raise HTTPException(404, "Application not found")
    require_case_token(record, token)
    return _refresh_payment_status(case_ref)


STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
SUMUP_WEBHOOK_SECRET = os.environ.get("SUMUP_WEBHOOK_SECRET", "")


def _verify_stripe_signature(raw_body: bytes, sig_header: str, secret: str, tolerance: int = 300) -> bool:
    """Stripe's documented manual verification: HMAC-SHA256 over
    "{timestamp}.{raw_body}" keyed with the endpoint signing secret, compared
    in constant time, with a timestamp freshness check to block replays.
    """
    if not sig_header:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp = parts.get("t")
    v1 = parts.get("v1")
    if not timestamp or not v1:
        return False
    if abs(time.time() - int(timestamp)) > tolerance:
        return False
    signed_payload = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return compare_digest(expected, v1)


@app.post("/api/webhooks/sumup")
async def sumup_webhook(request: Request):
    # SumUp webhook payload includes at least `id` (checkout id) and `event_type`.
    # No webhook endpoint is registered with SumUp yet, so there is no signing
    # secret to check against today — this is gated on SUMUP_WEBHOOK_SECRET so
    # verification switches on automatically once one is configured, rather
    # than silently trusting an unsigned payload forever. Either way, the
    # webhook body is only ever used to look up a checkout_id already in our
    # own database; the actual payment status always comes from calling
    # SumUp's own API back (_refresh_payment_status), never from the payload.
    raw_body = await request.body()
    if SUMUP_WEBHOOK_SECRET:
        sig = request.headers.get("x-sumup-signature", "")
        expected = hmac.new(SUMUP_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        if not sig or not compare_digest(expected, sig):
            raise HTTPException(400, "Invalid webhook signature")
    payload = json.loads(raw_body or b"{}")
    checkout_id = payload.get("id") or payload.get("checkout_id")
    if not checkout_id:
        return {"received": True, "note": "no checkout id in payload"}
    cur = db.execute("SELECT case_ref FROM applications WHERE payment_checkout_id = ?", [checkout_id])
    row = cur.fetchone()
    if row:
        _refresh_payment_status(row[0])
    return {"received": True}


@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request):
    # Stripe webhook payload wraps the checkout session under data.object and
    # tells us the event type (e.g. checkout.session.completed). No webhook
    # endpoint is registered with Stripe yet, so gated on STRIPE_WEBHOOK_SECRET
    # — verification switches on automatically once the dashboard secret is set,
    # instead of silently trusting an unsigned payload forever. As with SumUp,
    # the payload is only used to look up a checkout_id already in our own
    # database; the actual status always comes from calling Stripe's own API
    # back (_refresh_payment_status), never from the webhook body.
    raw_body = await request.body()
    if STRIPE_WEBHOOK_SECRET:
        sig_header = request.headers.get("stripe-signature", "")
        if not _verify_stripe_signature(raw_body, sig_header, STRIPE_WEBHOOK_SECRET):
            raise HTTPException(400, "Invalid webhook signature")
    payload = json.loads(raw_body or b"{}")
    session_obj = (payload.get("data") or {}).get("object") or {}
    checkout_id = session_obj.get("id") or payload.get("id")
    if not checkout_id:
        return {"received": True, "note": "no checkout session id in payload"}
    cur = db.execute("SELECT case_ref FROM applications WHERE payment_checkout_id = ?", [checkout_id])
    row = cur.fetchone()
    if row:
        _refresh_payment_status(row[0])
    return {"received": True}


@app.post("/api/applications/{case_ref}/verification")
def trigger_verification(case_ref: str, passcode: str = Query(...)):
    require_staff_passcode(passcode)
    record = get_application(case_ref)
    if not record:
        raise HTTPException(404, "Application not found")
    result = trustid_client.create_guest_link(
        first_name=record["first_name"] or (record["full_name"] or "").split(" ")[0],
        last_name=record["last_name"] or " ".join((record["full_name"] or "").split(" ")[1:]),
        email=record["email"],
        reference=case_ref,
    )
    db.execute(
        "UPDATE applications SET verification_status=?, verification_notes=?, updated_at=? WHERE case_ref=?",
        [result["status"], result["notes"], time.strftime("%Y-%m-%d %H:%M:%S"), case_ref],
    )
    db.commit()
    return get_application(case_ref)


@app.post("/api/webhooks/trustid")
async def trustid_webhook(payload: dict):
    # Placeholder — exact payload shape to be confirmed with TrustID's account
    # manager. Once known, look up the application by reference and update
    # verification_status to 'passed' / 'failed' / 'in_review' accordingly.
    return {"received": True, "note": "TrustID webhook handling not yet wired in"}


@app.get("/api/applications")
def list_applications(passcode: str = Query(...)):
    require_staff_passcode(passcode)
    cur = db.execute("SELECT * FROM applications ORDER BY id DESC")
    columns = [d[0] for d in cur.description]
    return [row_to_dict(r, columns) for r in cur.fetchall()]


@app.delete("/api/applications/{case_ref:path}")
def delete_application(case_ref: str, passcode: str = Query(...)):
    """Staff-only: permanently remove a single application record. Used to
    clean up test/demo submissions from the dashboard. There is no undo."""
    require_staff_passcode(passcode)
    record = get_application(case_ref)
    if not record:
        raise HTTPException(404, "Application not found")
    db.execute("DELETE FROM applications WHERE case_ref = ?", [case_ref])
    db.commit()
    return {"deleted": True, "case_ref": case_ref}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
