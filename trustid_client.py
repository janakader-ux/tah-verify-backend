"""
trustid_client.py — TrustID "Guest Link" self-verification client, using
TrustID's documented VPE "Raw API" (developer.trustid.co.uk/documentation/).

STATUS: Real implementation wired in. Requires four plain environment
variables to be set directly on the backend's hosting platform (Render,
Fly.io, Railway, a VM, etc. — wherever backend/api_server.py actually runs):

  TRUSTID_SERVER    e.g. https://cloud.trustid.co.uk   (no trailing slash)
  TRUSTID_USERNAME  the account's API login username
  TRUSTID_PASSWORD  the account's API login password
  TRUSTID_API_KEY   the account's static "Tid-Api-Key" header value

Why these must be plain env vars set on the real hosting platform, and not
one of this project's secure custom credentials: TrustID's login endpoint
(`/VPE/session/login/`) requires the username and password as plain JSON
body fields, not as an HTTP Authorization header or query parameter. The
custom-credential vault only auto-injects secrets into HTTP headers/query
strings on outbound requests — it has no mechanism to place a secret inside
a JSON request body — so it cannot be used for this specific login step.
The Tid-Api-Key IS a header and could be handled by the vault, but since the
whole flow needs to run together, all four values are read the same way for
consistency.

How the flow works (per TrustID's docs):
  1. POST {server}/VPE/session/login/ with DeviceId/Username/Password
     -> returns a SessionId (and the request must carry a stable DeviceId
     for the life of that session).
  2. POST {server}/VPE/guestLink/createGuestLink/ with header
     `Tid-Api-Key: <key>` and body containing SessionId, DeviceId, Email,
     Name, ClientApplicationReference (we pass our case_ref here), and
     SendEmail: true so TrustID emails the guest their verification link
     directly (set SendEmail to false and read `LinkUrl` from the response
     instead if you'd rather send the link yourselves).

Docs referenced:
  https://developer.trustid.co.uk/documentation/topics/auth3.html
  https://developer.trustid.co.uk/documentation/ref/raw_ref/request/session/login.html
  https://developer.trustid.co.uk/documentation/ref/raw_ref/request/guestLink/createGuestLink.html

Guest Links are only relevant to the "online" (remote/instant, TrustID
digital IDVT) route. Applicants on the "in-person" route are verified
manually by TAH staff at a booked appointment or scheduled video call and
never need a TrustID Guest Link.
"""
import os
import uuid
import requests

TRUSTID_SERVER = os.environ.get("TRUSTID_SERVER", "").rstrip("/")
TRUSTID_USERNAME = os.environ.get("TRUSTID_USERNAME", "")
TRUSTID_PASSWORD = os.environ.get("TRUSTID_PASSWORD", "")
TRUSTID_API_KEY = os.environ.get("TRUSTID_API_KEY", "")

# A single stable device identifier for this backend process, per TrustID's
# requirement that "once a session identifier has been obtained by using a
# specific device identifier, that identifier cannot change during the
# lifetime of the session."
_DEVICE_ID = os.environ.get("TRUSTID_DEVICE_ID", "") or str(uuid.uuid4())

# Manual portal fallback — staff create the Guest Link here by hand today,
# and this is always shown to staff as a backup regardless of API status.
TRUSTID_PORTAL_GUEST_LINK_URL = "https://cloud.trustid.co.uk/#/home/newguestLink/addGuestDetail"

_REQUEST_TIMEOUT = 20


def is_configured() -> bool:
    return bool(TRUSTID_SERVER and TRUSTID_USERNAME and TRUSTID_PASSWORD and TRUSTID_API_KEY)


def test_connection() -> dict:
    """
    Lightweight, unauthenticated reachability check against
    {server}/VPE/session/testConnection/. Useful for a staff diagnostics
    page — this does NOT confirm the username/password/API key are valid,
    only that the configured server address responds.
    """
    if not TRUSTID_SERVER:
        return {"reachable": False, "error": "TRUSTID_SERVER is not set."}
    try:
        resp = requests.post(f"{TRUSTID_SERVER}/VPE/session/testConnection/", timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {"reachable": bool(data.get("Success")), "raw": data}
    except requests.RequestException as exc:
        return {"reachable": False, "error": str(exc)}


def _login() -> dict:
    """
    POST /VPE/session/login/ — returns the parsed JSON response. Raises
    requests.HTTPError / requests.RequestException on transport failure.
    """
    resp = requests.post(
        f"{TRUSTID_SERVER}/VPE/session/login/",
        json={
            "DeviceId": _DEVICE_ID,
            "Username": TRUSTID_USERNAME,
            "Password": TRUSTID_PASSWORD,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def create_guest_link(first_name: str, last_name: str, email: str, reference: str) -> dict:
    """
    Returns a dict with keys: status, notes, guest_link_url (optional).

    status is one of:
      - "manual_action_required": credentials not configured — staff must
        create the Guest Link by hand from the TrustID portal.
      - "link_sent": TrustID emailed the guest their verification link
        directly.
      - "error": the API call failed; notes contains details and staff
        should fall back to the manual portal.
    """
    if not is_configured():
        return {
            "status": "manual_action_required",
            "notes": (
                f"Automated TrustID Guest Link not yet wired in (missing TRUSTID_SERVER / "
                f"TRUSTID_USERNAME / TRUSTID_PASSWORD / TRUSTID_API_KEY on the server). Staff: open "
                f"{TRUSTID_PORTAL_GUEST_LINK_URL} and create a Guest Link for "
                f"First name: {first_name} / Last name: {last_name} / Email: {email}, "
                f"quoting reference {reference}."
            ),
            "guest_link_url": None,
        }

    try:
        login_data = _login()
    except requests.RequestException as exc:
        return {
            "status": "error",
            "notes": (
                f"TrustID login failed ({exc}). Staff: open {TRUSTID_PORTAL_GUEST_LINK_URL} and create "
                f"a Guest Link manually for {first_name} {last_name} / {email}, reference {reference}."
            ),
            "guest_link_url": None,
        }

    if not login_data.get("Success"):
        return {
            "status": "error",
            "notes": (
                f"TrustID login was rejected: {login_data.get('Message', 'no message returned')}. "
                f"Staff: open {TRUSTID_PORTAL_GUEST_LINK_URL} and create a Guest Link manually for "
                f"{first_name} {last_name} / {email}, reference {reference}."
            ),
            "guest_link_url": None,
        }

    session_id = login_data.get("SessionId")
    full_name = f"{first_name} {last_name}".strip()

    try:
        resp = requests.post(
            f"{TRUSTID_SERVER}/VPE/guestLink/createGuestLink/",
            headers={"Tid-Api-Key": TRUSTID_API_KEY},
            json={
                "SessionId": session_id,
                "DeviceId": _DEVICE_ID,
                "Email": email,
                "Name": full_name,
                "ClientApplicationReference": reference,
                "SendEmail": True,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return {
            "status": "error",
            "notes": (
                f"TrustID Guest Link creation failed ({exc}). Staff: open "
                f"{TRUSTID_PORTAL_GUEST_LINK_URL} and create a Guest Link manually for "
                f"{full_name} / {email}, reference {reference}."
            ),
            "guest_link_url": None,
        }

    if not data.get("Success"):
        return {
            "status": "error",
            "notes": (
                f"TrustID Guest Link creation was rejected: {data.get('Message', 'no message returned')}. "
                f"Staff: open {TRUSTID_PORTAL_GUEST_LINK_URL} and create a Guest Link manually for "
                f"{full_name} / {email}, reference {reference}."
            ),
            "guest_link_url": None,
        }

    return {
        "status": "link_sent",
        "notes": f"Guest Link emailed by TrustID directly to {email}.",
        "guest_link_url": data.get("LinkUrl"),
    }
