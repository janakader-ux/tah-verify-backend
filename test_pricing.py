#!/usr/bin/env python3
"""Regression tests for server-side fee validation.

Run from this directory:  python3 test_pricing.py

These guard the fix for the 2 Sep 2026 vulnerability where the browser-supplied
`fee_amount` was charged verbatim, letting an overseas director pay the £49 UK
remote price. Every case below must keep passing whenever pricing changes: if
you edit PRICING in api_server.py, update the expected values here too.

Uses a throwaway SQLite file, so it never touches real application data.
"""
import os, sys, tempfile, importlib
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, os.getcwd())
import api_server as A
try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError) as exc:  # pragma: no cover
    sys.exit(
        f"Cannot start the test client: {exc}\n\n"
        "These tests need httpx, which is a TEST-ONLY dependency and is\n"
        "deliberately kept out of requirements.txt so it is never installed on\n"
        "the production Render service. Install it locally with:\n\n"
        "    pip install httpx\n"
    )
c = TestClient(A.app)

def post(case, residency, route, fee):
    body = {"case_ref": case, "full_name": "Test D", "email": "t@example.com",
            "residency": residency, "fee_amount": fee}
    if route is not None: body["route"] = route
    return c.post("/api/applications", json=body)

fails = []
def check(label, got, want):
    ok = got == want
    if not ok: fails.append(label)
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")

print("--- honest submissions ---")
for i,(res,rt,exp) in enumerate([("uk","online",49.0),("uk","in-person",125.0),
                                 ("overseas","online",175.0),("overseas","in-person",175.0)]):
    r = post(f"H{i}", res, rt, exp)
    check(f"{res}/{rt}", r.json().get("fee_amount"), exp)

print("\n--- THE ATTACK: overseas director claims to pay 49 ---")
r = post("ATK1", "overseas", "online", 49.0)
check("tampered overseas -> forced to 175", r.json().get("fee_amount"), 175.0)

r = post("ATK2", "uk", "in-person", 49.0)
check("tampered UK in-person -> forced to 125", r.json().get("fee_amount"), 125.0)

r = post("ATK3", "overseas", "online", 0.01)
check("penny attack -> forced to 175", r.json().get("fee_amount"), 175.0)

r = post("ATK4", "overseas", "online", -175.0)
check("negative amount -> forced to 175", r.json().get("fee_amount"), 175.0)

print("\n--- fee omitted entirely ---")
r = c.post("/api/applications", json={"case_ref":"OMIT","full_name":"T","email":"t@e.com",
                                      "residency":"overseas","route":"online"})
check("no fee_amount sent", r.json().get("fee_amount"), 175.0)

print("\n--- bad input rejected ---")
r = post("BAD1", "mars", "online", 49.0)
check("unknown residency -> 400", r.status_code, 400)
r = post("BAD2", "uk", "free", 0.0)
check("unknown route -> 400", r.status_code, 400)

print("\n--- fail-safe: legacy row with no route ---")
r = post("LEG1", "uk", None, 125.0)
check("uk, no route -> highest UK tier 125", r.json().get("fee_amount"), 125.0)
r = post("LEG2", "overseas", None, 175.0)
check("overseas, no route -> 175", r.json().get("fee_amount"), 175.0)

print("\n--- update path (re-POST same case_ref) cannot downgrade ---")
r = post("ATK1", "overseas", "online", 49.0)
check("re-POST tamper -> still 175", r.json().get("fee_amount"), 175.0)

print("\n--- charge-time guard on a poisoned legacy DB row ---")
A.db.execute("UPDATE applications SET fee_amount=1.0 WHERE case_ref='ATK1'"); A.db.commit()
rec = A.get_application("ATK1")
check("stored 1.00 but charge_amount_for", A.charge_amount_for(rec), 175.0)
check("stripe unit_amount (pence)", int(round(A.charge_amount_for(rec)*100)), 17500)

print("\n" + ("ALL TESTS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
