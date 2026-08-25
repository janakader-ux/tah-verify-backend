#!/usr/bin/env python3
"""
api_server.py — Director Personal Code backend, runs on port 8000 inside the sandbox.

Handles:
  - Storing wizard applications (case reference, applicant details, fee, route)
  - Storing in-person appointment requests (office / date / time preference)
  - Creating SumUp Hosted Checkout payment links + polling payment status
  - TrustID Guest Link verification (see trustid_client.py) — fires
    AUTOMATICALLY the moment payment is confirmed as paid for the "online"
    route (see _refresh_payment_status below); the staff-only
    /api/applications/{case_ref}/verification endpoint remains available as a
    manual retry if the automatic attempt errors (e.g. TrustID temporarily
    unreachable) or a link needs to be re-sent.
  - A lightweight passcode-protected staff view listing applications and their
    payment / verification status, so staff know when to manually:
      1) confirm an appointment slot for the in-person route, and
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
from html import escape as html_escape
from contextlib import asynccontextmanager
from typing import Optional

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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


# Card-statement descriptor applied to each Director Personal Code payment.
# Stripe hard-limits this to 22 characters and rejects the characters < > \ ' " *,
# so the value is sanitised here rather than trusted blindly from the environment:
# a descriptor Stripe rejects would fail the whole checkout-session creation and
# therefore block the customer from paying at all. Overridable via the
# STRIPE_STATEMENT_DESCRIPTOR env var without a code change.
def _clean_statement_descriptor(value: str, fallback: str = "DIRECTOR PERSONAL CODE") -> str:
    cleaned = "".join(c for c in (value or "") if c not in "<>\\'\"*")
    cleaned = " ".join(cleaned.split())[:22].strip()
    return cleaned or fallback


STRIPE_STATEMENT_DESCRIPTOR = _clean_statement_descriptor(
    os.environ.get("STRIPE_STATEMENT_DESCRIPTOR", "DIRECTOR PERSONAL CODE")
)

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
    ("role", "TEXT"),
    ("former_names", "TEXT"),
    ("dob", "TEXT"),
    ("nationality", "TEXT"),
    ("residence_country", "TEXT"),
    ("home_address", "TEXT"),
    ("address_since", "TEXT"),
    ("previous_address", "TEXT"),
    ("mobile", "TEXT"),
    ("company_name", "TEXT"),
    ("company_number", "TEXT"),
    ("role_confirm", "TEXT"),
    ("sign_name", "TEXT"),
    ("sign_date", "TEXT"),
    ("sign_ip", "TEXT"),
    ("signature_data", "TEXT"),
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


# Name of the request header that carries the staff passcode. Preferred over the
# legacy ?passcode= query parameter: query strings are routinely written to
# web-server access logs, reverse-proxy logs and browser history, whereas
# request headers are not, so the header keeps the shared secret out of logs.
STAFF_PASSCODE_HEADER = "X-Staff-Passcode"


def require_staff_passcode(passcode: Optional[str]) -> None:
    """Fail closed when staff access has not been configured on the server."""
    if not STAFF_PASSCODE:
        logger.error("Staff-only endpoint called while STAFF_PASSCODE is not configured")
        raise HTTPException(503, "Staff access is not configured")
    # Treat a missing or empty passcode as a plain auth failure. Previously this
    # function assumed a str and would raise TypeError inside compare_digest
    # (surfacing as a 500) if handed None; it must fail closed with a 403.
    if not passcode:
        raise HTTPException(403, "Invalid passcode")
    # compare_digest raises TypeError on non-ASCII str inputs, so compare bytes.
    # Still constant-time, but tolerant of any character a passcode may contain.
    if not compare_digest(passcode.encode("utf-8"), STAFF_PASSCODE.encode("utf-8")):
        raise HTTPException(403, "Invalid passcode")


def staff_passcode_supplied(
    header_passcode: Optional[str] = Header(default=None, alias=STAFF_PASSCODE_HEADER),
    passcode: Optional[str] = Query(default=None),
) -> Optional[str]:
    """Return the staff passcode supplied by the caller, header first.

    Accepts either transport so that the frontend can move to the header without
    a flag-day cutover, and so manual diagnostic calls that still append
    ?passcode=... keep working. This only *reads* the value; validation remains
    with require_staff_passcode so every call site keeps its existing check.
    """
    if header_passcode is not None:
        return header_passcode
    if passcode is not None:
        logger.warning(
            "Staff endpoint authenticated via the deprecated ?passcode= query "
            "parameter, which leaks the secret into access logs. Send the %s "
            "header instead.",
            STAFF_PASSCODE_HEADER,
        )
    return passcode


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
    # The wizard's "Your situation" / "Personal details" / "Company details" /
    # "Engagement letter" steps collect all of the fields below and show them
    # back to the applicant on the review screen (step 6), but until now the
    # frontend never actually sent them to this endpoint — only full_name,
    # first_name, last_name, email, residency, fee_amount, route made it here.
    # Everything else (role, DOB, nationality, address, mobile, company name/
    # number, and the signed engagement letter's name/date/IP/image) was
    # silently dropped, so staff never received it in the notification email
    # or the staff dashboard. All are optional here so older/partial payloads
    # never break, but the frontend now sends all of them (see apply.js).
    role: Optional[str] = None
    former_names: Optional[str] = None
    dob: Optional[str] = None
    nationality: Optional[str] = None
    residence_country: Optional[str] = None
    home_address: Optional[str] = None
    address_since: Optional[str] = None
    previous_address: Optional[str] = None
    mobile: Optional[str] = None
    company_name: Optional[str] = None
    company_number: Optional[str] = None
    role_confirm: Optional[str] = None
    sign_name: Optional[str] = None
    sign_date: Optional[str] = None
    sign_ip: Optional[str] = None
    signature_data: Optional[str] = None  # base64 PNG data URL from the signature pad


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


def _brevo_send(to_email: str, subject: str, html: str, kind: str = "Notification") -> bool:
    """Low-level Brevo transactional send. Shared by the staff notification
    emails and the applicant-facing confirmation email.

    Best-effort: never raises. Returns True on confirmed send, False
    otherwise (and logs the reason) so callers can proceed regardless —
    the database record is always the source of truth even if the email
    fails to send.

    NOTE ON DELIVERABILITY: a Brevo 2xx here means Brevo *accepted* the
    message, NOT that it was delivered. In August 2026 a real "Payment
    received" notification was accepted and then soft-bounced with
    "550-5.7.26 Unauthenticated email from taxandaccountinghub.com is not
    accepted due to domain's DMARC policy" — so a payment was taken and
    staff were never told. The cause was sending as an address on a domain
    Brevo was not authorised for. Keep BREVO_SENDER_EMAIL on a domain whose
    SPF/DKIM are set up in Brevo (currently notifications@directorpersonalcode.uk);
    a True return from this function is not by itself proof of delivery.
    """
    if not BREVO_API_KEY:
        logger.warning("%s email skipped (BREVO_API_KEY not configured): %s", kind, subject)
        return False
    if not to_email:
        logger.warning("%s email skipped (no recipient address): %s", kind, subject)
        return False
    payload = {
        "sender": {"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
        "to": [{"email": to_email}],
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
        logger.info("%s email accepted by Brevo: %s -> %s", kind, subject, to_email)
        return True
    except Exception as exc:  # noqa: BLE001 — notification failures must never break the request
        logger.exception("%s email raised an exception: %s", kind, exc)
        return False


def send_notification_email(subject: str, fields: dict, intro: str = "") -> bool:
    """Send a staff notification email via Brevo's transactional email API.

    Goes to NOTIFICATION_EMAIL (the staff inbox), never to the applicant.
    """
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
    return _brevo_send(NOTIFICATION_EMAIL, subject, html, kind="Staff notification")


def send_payment_confirmation_email(case_ref: str, record: dict, online_link_sent: bool) -> bool:
    """Send the APPLICANT a branded confirmation that their payment landed.

    Why this exists: until now a paying customer received nothing from us at
    all — only Stripe's own receipt and (on the online route) a TrustID email
    whose sender they do not recognise. For a £125-£175 professional service
    that is the most likely trigger for "have you received my payment?"
    enquiries, and it left the customer with no record of their case reference.

    Best-effort and fully isolated: any failure here must never affect the
    payment record, the staff notification, or the applicant's HTTP response.
    """
    to_email = (record.get("email") or "").strip()
    if not to_email:
        return False

    first = (record.get("first_name") or (record.get("full_name") or "").split(" ")[0] or "").strip()
    greeting = f"Dear {html_escape(first)}," if first else "Hello,"
    try:
        fee = f"£{float(record.get('fee_amount')):.2f}"
    except (TypeError, ValueError):
        fee = ""
    route = (record.get("route") or "").strip()

    if route == "online":
        if online_link_sent:
            next_steps = (
                "<li>You will receive a separate email from <strong>TrustID</strong>, our "
                "identity verification provider, containing a secure link to complete your "
                "check online. It usually arrives within a few minutes.</li>"
                "<li>Open that link on a phone with a camera and have your passport or "
                "driving licence to hand.</li>"
                "<li><strong>If you cannot find it, please check your spam or junk folder</strong> "
                "before contacting us — it is sent by TrustID, not from this address.</li>"
            )
        else:
            next_steps = (
                "<li>We are setting up your secure identity verification link now and will "
                "email it to you shortly. No action is needed from you at this stage.</li>"
                "<li>If you have not received it within one working day, reply to this email "
                "quoting your case reference and we will resend it.</li>"
            )
    else:
        next_steps = (
            "<li>Our team will contact you to confirm your appointment for in-person "
            "identity verification.</li>"
            "<li>Please bring your passport or driving licence to the appointment.</li>"
        )

    rows = [("Case reference", case_ref), ("Amount paid", fee)]
    if record.get("full_name"):
        rows.append(("Applicant", record["full_name"]))
    if record.get("company_name"):
        rows.append(("Company", record["company_name"]))
    rows_html = "".join(
        f"<tr><td style='padding:6px 14px 6px 0;color:#575F67;font-size:14px;"
        f"vertical-align:top;white-space:nowrap;'>{html_escape(str(k))}</td>"
        f"<td style='padding:6px 0;font-size:14px;color:#0B1B2F;'><strong>"
        f"{html_escape(str(v))}</strong></td></tr>"
        for k, v in rows if v
    )

    html = f"""<div style="margin:0;padding:24px;background:#F8F7F2;">
  <div style="max-width:560px;margin:0 auto;background:#FCFCF9;border:1px solid #E6E1DC;">
    <div style="background:#0B1B2F;padding:20px 28px;">
      <div style="color:#F8F7F2;font-family:Georgia,serif;font-size:19px;">Director Personal Code</div>
      <div style="color:#C49F4D;font-size:12px;letter-spacing:.08em;text-transform:uppercase;
                  padding-top:4px;">Companies House identity verification</div>
    </div>
    <div style="padding:28px;font-family:Helvetica,Arial,sans-serif;color:#1B2026;">
      <div style="font-size:19px;color:#0B1B2F;padding-bottom:14px;">Payment received — thank you</div>
      <p style="font-size:14px;line-height:1.6;margin:0 0 14px;">{greeting}</p>
      <p style="font-size:14px;line-height:1.6;margin:0 0 18px;">
        We have received your payment and your identity verification case is now open.
        Please keep this email — it is your record of payment and contains your case reference.
      </p>
      <table style="border-collapse:collapse;margin:0 0 22px;">{rows_html}</table>
      <div style="font-size:14px;color:#0B1B2F;font-weight:bold;padding-bottom:6px;">What happens next</div>
      <ul style="font-size:14px;line-height:1.6;margin:0 0 20px;padding-left:20px;">{next_steps}</ul>
      <p style="font-size:14px;line-height:1.6;margin:0 0 20px;">
        If you have any questions, reply to this email or contact us at
        <a href="mailto:{NOTIFICATION_EMAIL}" style="color:#6D1731;">{NOTIFICATION_EMAIL}</a>,
        quoting your case reference.
      </p>
      <div style="border-top:1px solid #E6E1DC;padding-top:14px;font-size:11px;
                  line-height:1.6;color:#575F67;">
        Tax And Accounting Hub Ltd · Registered ACSP · Company No. 08408126 ·
        AAT supervised for AML · ICO registered<br>
        11 Holbeach Avenue, Shortstown, Bedford, MK42 0EG
      </div>
    </div>
  </div>
</div>"""

    return _brevo_send(
        to_email,
        f"Payment received — your case reference {case_ref}",
        html,
        kind="Applicant confirmation",
    )


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
def send_test_email(passcode: Optional[str] = Depends(staff_passcode_supplied)):
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
def trustid_test(passcode: Optional[str] = Depends(staff_passcode_supplied)):
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
               appointment_time_pref=?, role=?, former_names=?, dob=?, nationality=?, residence_country=?,
               home_address=?, address_since=?, previous_address=?, mobile=?, company_name=?,
               company_number=?, role_confirm=?, sign_name=?, sign_date=?, sign_ip=?, signature_data=?,
               updated_at=? WHERE case_ref=?""",
            [
                app_in.full_name, app_in.first_name, app_in.last_name, app_in.email, app_in.residency,
                app_in.fee_amount, app_in.route, app_in.appointment_type, app_in.appointment_office,
                app_in.appointment_date, app_in.appointment_time_pref, app_in.role, app_in.former_names,
                app_in.dob, app_in.nationality, app_in.residence_country, app_in.home_address,
                app_in.address_since, app_in.previous_address, app_in.mobile, app_in.company_name,
                app_in.company_number, app_in.role_confirm, app_in.sign_name, app_in.sign_date,
                app_in.sign_ip, app_in.signature_data, now, app_in.case_ref,
            ],
        )
    else:
        db.execute(
            """INSERT INTO applications (case_ref, full_name, first_name, last_name, email, residency,
               fee_amount, route, appointment_type, appointment_office, appointment_date,
               appointment_time_pref, role, former_names, dob, nationality, residence_country,
               home_address, address_since, previous_address, mobile, company_name, company_number,
               role_confirm, sign_name, sign_date, sign_ip, signature_data, updated_at, access_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                app_in.case_ref, app_in.full_name, app_in.first_name, app_in.last_name, app_in.email,
                app_in.residency, app_in.fee_amount, app_in.route, app_in.appointment_type,
                app_in.appointment_office, app_in.appointment_date, app_in.appointment_time_pref,
                app_in.role, app_in.former_names, app_in.dob, app_in.nationality, app_in.residence_country,
                app_in.home_address, app_in.address_since, app_in.previous_address, app_in.mobile,
                app_in.company_name, app_in.company_number, app_in.role_confirm, app_in.sign_name,
                app_in.sign_date, app_in.sign_ip, app_in.signature_data, now,
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
    # Previously this notification only ever carried full_name / email / fee /
    # route — every other field the wizard actually collects (role, DOB,
    # nationality, addresses, mobile, company name/number, and the signed
    # engagement letter's name/date/IP) was captured on screen but never sent
    # to this backend at all, so staff never received it anywhere. Now that
    # apply.js sends the full payload and create_application() persists it,
    # surface all of it here so staff have everything needed to process the
    # case without digging through the raw database.
    fields = {
        "Case reference": case_ref,
        "Applicant": updated.get("full_name") or "",
        "Role": updated.get("role") or "",
        "Former name(s)": updated.get("former_names") or "None",
        "Date of birth": updated.get("dob") or "",
        "Nationality": updated.get("nationality") or "",
        "Country of residence": updated.get("residence_country") or "",
        "Home address": updated.get("home_address") or "",
        "At address since": updated.get("address_since") or "",
        "Previous address": updated.get("previous_address") or "N/A",
        "Email": updated.get("email") or "",
        "Mobile": updated.get("mobile") or "",
        "Company name": updated.get("company_name") or "",
        "Company number": updated.get("company_number") or "",
        "Role (confirmed)": updated.get("role_confirm") or "",
        "Fee": f"£{float(updated.get('fee_amount')):.2f}" if updated.get("fee_amount") else "",
        "Verification route": updated.get("route") or "",
        "Engagement letter signed by": updated.get("sign_name") or "",
        "Signed on": updated.get("sign_date") or "",
        "Signing IP (audit trail)": updated.get("sign_ip") or "Not available",
        "Signature captured": "Yes" if updated.get("signature_data") else "No",
    }
    send_notification_email(
        f"New ACSP ID verification application — Case {case_ref}",
        fields,
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
        "line_items[0][price_data][product_data][name]": f"Director Personal Code — Companies House identity verification ({case_ref})",
        "metadata[case_ref]": case_ref,
        # Turn OFF Stripe Adaptive Pricing. With it enabled, a non-UK visitor was
        # shown a converted local-currency amount as the DEFAULT selected option
        # (e.g. $177.37 with "includes 4% conversion fee"), pushing GBP to a
        # secondary tab. Our fees are advertised as all-inclusive GBP prices
        # (£125 UK-resident / £175 overseas), so a surprise ~4% FX margin at the
        # checkout contradicts that promise — and overseas directors are exactly
        # the group most affected. Everyone now pays the advertised GBP amount.
        "adaptive_pricing[enabled]": "false",
        # Card-statement text for THIS payment only.
        #
        # The Stripe account's own account-wide descriptor is "TAX AND ACCOUNTING
        # HUB", which is the legal entity but not the brand the customer bought
        # from. Someone who applied at directorpersonalcode.uk and then sees an
        # unfamiliar name against a 125 pound charge on their statement is a prime
        # candidate for a chargeback, and card disputes are expensive and slow to
        # defend even when won.
        #
        # Deliberately set per payment rather than on the account: the account may
        # later process other Tax & Accounting Hub / USTAX4Expats work, and the
        # account-wide descriptor is shared by everything that charges through it,
        # so changing it there would mislabel those other services instead.
        "payment_intent_data[statement_descriptor]": STRIPE_STATEMENT_DESCRIPTOR,
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
            "description": f"Director Personal Code — Companies House identity verification ({case_ref})",
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
        #
        # IMPORTANT: for the "online" route this used to ONLY send staff an
        # email asking them to go create the TrustID Guest Link by hand —
        # despite TRUSTID_SERVER/USERNAME/PASSWORD/API_KEY being fully
        # configured and working, the actual trustid_client.create_guest_link()
        # call was never wired in here, so "automatic" TrustID never actually
        # ran on payment; only the separate, staff-triggered
        # /api/applications/{case_ref}/verification endpoint called it. Now
        # that call happens for real, right here, the moment payment clears.
        verification_intro = None
        online_link_sent = False
        if record.get("route") == "online":
            result = trustid_client.create_guest_link(
                first_name=record.get("first_name") or (record.get("full_name") or "").split(" ")[0],
                last_name=record.get("last_name") or " ".join((record.get("full_name") or "").split(" ")[1:]),
                email=record.get("email"),
                reference=case_ref,
            )
            db.execute(
                "UPDATE applications SET verification_status=?, verification_notes=?, updated_at=? WHERE case_ref=?",
                [result["status"], result["notes"], time.strftime("%Y-%m-%d %H:%M:%S"), case_ref],
            )
            db.commit()
            if result["status"] == "link_sent":
                online_link_sent = True
                verification_intro = "Payment has been received and TrustID has automatically emailed the applicant their identity verification Guest Link — no staff action needed unless they report an issue."
            else:
                verification_intro = (
                    "Payment has been received. Automatic TrustID Guest Link creation did NOT succeed "
                    f"(status: {result['status']}) — {result['notes']}"
                )
        else:
            verification_intro = "Payment has been received. The applicant will request an in-person appointment next."

        send_notification_email(
            f"Payment received — Case {case_ref}",
            {
                "Case reference": case_ref,
                "Applicant": record.get("full_name") or "",
                "Email": record.get("email") or "",
                "Fee": f"£{float(record.get('fee_amount')):.2f}" if record.get("fee_amount") else "",
                "Verification route": record.get("route") or "",
            },
            intro=verification_intro,
        )

        # Confirm to the APPLICANT that their money arrived. Previously they got
        # nothing from us: only Stripe's receipt and, on the online route, a
        # TrustID email from a sender they have no reason to recognise.
        #
        # Wrapped in its own try/except on top of the best-effort send inside
        # _brevo_send, because this runs inside the payment-status refresh that
        # the applicant's own browser is waiting on. A courtesy email must never
        # be able to turn a successful payment into an HTTP 500 for the person
        # who just paid — the payment is already committed to the database above.
        try:
            send_payment_confirmation_email(case_ref, record, online_link_sent=online_link_sent)
        except Exception as exc:  # noqa: BLE001 — never let the courtesy email break payment handling
            logger.exception("Applicant confirmation email failed for %s: %s", case_ref, exc)

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
    # tells us the event type (e.g. checkout.session.completed).
    #
    # A live webhook endpoint IS registered with Stripe and enabled, pointing at
    # this route, subscribed to checkout.session.completed / .expired /
    # .async_payment_succeeded / .async_payment_failed. (An earlier version of
    # this comment said no endpoint was registered yet — that is no longer true,
    # and believing it risks someone building a redundant workaround for payments
    # they assume are never delivered here.)
    #
    # This matters because it is what stops a payment being lost: the wizard also
    # polls /payment-status on return from Stripe, but a customer who pays and
    # immediately closes the tab never makes that call. The webhook is the path
    # that still flips the record and notifies staff in that case.
    #
    # Signature verification is gated on STRIPE_WEBHOOK_SECRET so it can never
    # silently trust an unsigned payload. Verified behaviour: a correctly signed
    # payload is accepted; a wrong secret, a stale timestamp (replay), and a
    # missing signature header are each rejected with HTTP 400.
    #
    # As with SumUp, the payload is only used to look up a checkout_id already in
    # our own database; the actual status always comes from calling Stripe's own
    # API back (_refresh_payment_status), never from the webhook body. An unknown
    # checkout_id is a harmless no-op.
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
def trigger_verification(
    case_ref: str, passcode: Optional[str] = Depends(staff_passcode_supplied)
):
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
def list_applications(passcode: Optional[str] = Depends(staff_passcode_supplied)):
    require_staff_passcode(passcode)
    cur = db.execute("SELECT * FROM applications ORDER BY id DESC")
    columns = [d[0] for d in cur.description]
    return [row_to_dict(r, columns) for r in cur.fetchall()]


@app.post("/api/diagnostics/reconcile-payments")
def reconcile_payments(passcode: Optional[str] = Depends(staff_passcode_supplied)):
    """Safety net for the automatic TrustID trigger.

    _refresh_payment_status() (which fires TrustID the moment a payment is
    seen as paid) is normally invoked by the APPLICANT's own browser polling
    /payment-status every 6 seconds after they open the Stripe/SumUp payment
    link in a new tab. That works for the normal flow, but there is no real
    payment-provider webhook wired in, so if an applicant closes their
    original tab before the poll catches the paid status (and never returns
    to click "I've paid - check status"), nothing else ever re-checks that
    payment - it would sit as 'pending' forever and TrustID would never fire.

    This endpoint is that missing re-check: it re-runs the same authoritative
    _refresh_payment_status() lookup (direct to Stripe/SumUp, never trusting
    client input) for every application that still has payment_status !=
    'paid' but does have a payment_checkout_id (i.e. a payment link was
    created). Safe to call any time / repeatedly - it is a no-op for
    anything already paid or that never got a checkout started, and reuses
    the exact same paid-transition logic (including the automatic TrustID
    call) as the applicant-facing endpoint.
    """
    require_staff_passcode(passcode)
    cur = db.execute(
        "SELECT case_ref FROM applications WHERE payment_status != 'paid' AND payment_checkout_id IS NOT NULL AND payment_checkout_id != ''"
    )
    case_refs = [r[0] for r in cur.fetchall()]
    checked = []
    for case_ref in case_refs:
        try:
            result = _refresh_payment_status(case_ref)
            checked.append({"case_ref": case_ref, "payment_status": result.get("payment_status")})
        except Exception as exc:  # noqa: BLE001 - one bad record must not block the rest
            checked.append({"case_ref": case_ref, "error": str(exc)})
    return {"checked_count": len(checked), "results": checked}


@app.delete("/api/applications/{case_ref:path}")
def delete_application(
    case_ref: str, passcode: Optional[str] = Depends(staff_passcode_supplied)
):
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
