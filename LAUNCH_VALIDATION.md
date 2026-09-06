# Revised application workflow — 6 September 2026

Application → signed engagement → review and submit → secure payment → TrustID guest link (remote) or paid appointment request (office) → staff review → Companies House submission by staff.

No account registration, preliminary document upload or AI review is required. Existing preliminary-review flags no longer block checkout. Archived document-review records retain their original protections and expiry.

All four real API workflows pass with external providers mocked: UK remote £49, overseas remote £119, UK office £125, overseas office £125. Coverage includes missing submission, unpaid/expired/paid checkout, fixed authoritative fees, no duplicate paid checkout, TrustID only after payment, office appointment gating and staff paid status. Frontend tests cover unsigned/incomplete applications and save/submission failures.

TrustID result notifications now use documented per-Guest-Link callback URLs and authentication headers. Authenticated AutoReferral/Stop callbacks queue staff review, not identity approval. A durable outbox retries failed notification email and suppresses repeated callbacks. Tests cover authentication, paid-route restriction, duplicate delivery and retry. Existing guest links created before this release do not acquire the new callback automatically; staff must monitor those in TrustID.

Sources: https://developer.trustid.co.uk/documentation/topics/guestLink.html and https://developer.trustid.co.uk/documentation/topics/webhookcallback4.html

Production-provider limitations: dummy tests cannot certify real Stripe charges, TrustID account permissions, guest-link email delivery or provider callbacks. A controlled provider transaction is required for that sign-off. No real charges, identity submissions or diagnostic emails were sent during these tests.

Browser preview: all four categories reached final confirmation with dummy paid status and zero document-upload inputs. Missing role, route, first name and invalid email were blocked with named prompts. Declined dummy payment stayed locked. Both office requests completed. Mobile 390px navigation and missing-role prompt passed. Production fixtures remain excluded.
