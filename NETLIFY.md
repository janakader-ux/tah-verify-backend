# Director Personal Code deployment

The original marketing website and nine-step application wizard live in `public/`.
Netlify publishes that directory as a static site with no build command. The root
Python application continues to run on Render; it is not part of the public site.

## Recovery provenance

Recovered from the supplied `HANDOFF-directorpersonalcode-source.zip` and the
2 September 2026 Netlify deployment `6a9895e4b3f3fdebdedbe1c2`.
All 61 original HTML pages, CSS, JavaScript, crawler files and `_headers` are restored.
54 missing images, posters and video files were recovered from that immutable deploy.
Their hashes are recorded in `recovery-media-manifest.json`.

The original visual design is preserved. The supplied DPC logo is used in page branding
with WebP artwork; a traced SVG version is also available in `public/assets/`.
The original application JavaScript calls the existing Render backend and uses the
original UK online £49, UK in-person £125 and overseas £175 pricing matrix.
The database, environment variables and payment credentials are unchanged.
Backend fixes protect existing application updates with the case token, lock records
after checkout starts, reuse pending checkout sessions and reject payment on paid cases.
Frontend submission and appointment handlers now check HTTP errors before showing success.

## Correct deployment settings

- Repository: `janakader-ux/tah-verify-backend`
- Production branch: `master`
- Base directory: repository root
- Build command: empty
- Publish directory: `public`

The earlier recovery published a placeholder form from the backend repository.
Those root placeholder HTML files and the temporary static build script are removed
to avoid accidentally deploying them again. Always deploy `public/` in full.
Do not publish internal handoff documents, backend files or database exports.

## Validation limits

Check the homepage, application wizard navigation, representative content pages,
images and videos after deployment. No real applicant data, payments, emails or
identity checks should be submitted just to verify a frontend recovery.

## Regression checks and remaining integration limits

CI runs pricing, case access, mocked checkout/payment/TrustID/email/appointment flows,
frontend submission HTTP error cases, JavaScript syntax and all local page/resource links.
Mocked providers never charge cards or send messages.

The live health endpoint reports configured Stripe, TrustID and email integrations.
Actual delivery and a live payment have not been exercised. TrustID result webhooks
remain a placeholder in the inherited backend; final verification review is a staff
operation, not a fully automated Companies House filing. Do not describe the workflow
as fully automated until the provider webhook contract and staff approval process are
implemented and tested. Revenue depends on traffic, conversion, service capacity and costs.
