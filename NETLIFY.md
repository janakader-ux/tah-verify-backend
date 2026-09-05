# Netlify deployment

Connect this repository and select production branch `master`. Leave the package
directory unset. The root `netlify.toml` sets base `.`, build command
`python3 scripts/build_site.py`, and publish directory `dist`.

Run that command locally to reproduce the static output. Only public HTML and logo assets are
published. Both `/` and `/apply.html` serve the application directly, including
requests with `?residency=overseas` or `?route=b2b`. The source `index.html` remains
a fallback for other static hosts; Netlify no longer relies on its redirect.

## Deployment verification

After merging, check the Netlify deploy log for the new commit and the output
`Built dist/index.html, dist/apply.html and assets`. Test the Netlify-provided site URL,
then `https://directorpersonalcode.uk` and the `www` hostname. Add both custom
hostnames to the same Netlify project, verify DNS using the records Netlify
provides, and confirm the HTTPS certificate covers both. Do not guess DNS targets.

## Findings and remaining blockers

On 5 September 2026 the public non-www homepage and `/apply.html` could be
retrieved. The homepage was a redirect stub. The www lookup could not be verified.
GitHub's latest test passed; no Netlify commit status was reported. Without the
Netlify deploy log, an account-level build, domain, or TLS failure is unconfirmed.

The application HTML had duplicate document/head openings and duplicate Google
tag initialization; these are corrected. Browsers often recover from malformed
HTML, so this alone does not establish the cause of an outage.

This repository does not contain the original marketing homepage or the full
application wizard mentioned in backend comments. Its application submit handler
only shows an alert and sends no request. Restoring a full verification journey
requires the original frontend or a separately implemented and tested integration.
Do not regard loading the static form as working payment or identity verification.

The Python FastAPI service is configured by `render.yaml` for Render, including
persistent data storage. This Netlify build does not run that API. Confirm the
actual backend URL, allowed frontend origins, and intended pricing before wiring
up the form: the frontend and backend currently have different route/fee models.
The existing form-submission conversion event also is not evidence of a successful
application or payment. No production submissions or payments were made in testing.

## Logo assets

`assets/dpc-logo.webp` is a lossless conversion of the supplied transparent PNG.
`assets/dpc-logo.svg` is an automatically traced vector version on white; its
curves and gradients approximate the PNG. The page uses the faithful WebP, and
the SVG is available as a scalable asset and favicon. Both are copied by the
static build.
