# Application validation — 6 September 2026

Four application categories passed the real API test matrix with external providers mocked, and browser journeys on the isolated Netlify preview:

| Category | Expected fee | API workflow | Browser completion |
| --- | ---: | --- | --- |
| UK remote | £49 | Pass | Pass |
| Overseas remote | £119 | Pass | Pass |
| UK office | £125 | Pass | Pass |
| Overseas office | £125 | Pass | Pass |

Coverage: application details and engagement signature; preliminary document upload and staff approval before checkout; authoritative fees; pending/expired/paid payment states; no repeated charge creation after payment; staff paid status; remote verification handoff; paid-only office appointment requests; final confirmation. Browser tests also rejected an unpaid/declined checkout and an empty office selection. The mobile 390px application retains visible Next navigation and selection guidance.

Fixes: fixed bottom navigation above the cookie banner, explicit missing-selection instructions, native required-field validity, retry for failed/expired checkout, server-side paid and route checks before appointment booking, valid future appointment dates, and consistent two-original-document office instructions.

Fixtures are generated only for Netlify deploy-preview builds and never included in production. They use synthetic data and simulated payment, identity-provider and email responses. No real customer records, card charges or identity submissions were used. Automated tests run in GitHub Actions.

Limits: this is not live Stripe/TrustID/email-provider certification. A controlled provider-sandbox or live transaction is still needed before claiming the complete production provider chain is verified. AI preliminary review remains unavailable until its server API credential is configured; staff preliminary review is the supported current route. Preliminary review is not identity verification.
