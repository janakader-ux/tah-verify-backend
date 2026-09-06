# Pricing and preliminary document review

Individual all-inclusive fees: UK remote £49, overseas remote £119, standard office appointment £125 for either residency group. Complex cases start at £175 after assessment and agreement. Bulk remote fees are £39 UK / £85 overseas, minimum 10 applicants per order including mixed groups. Larger orders and visits are enquiries, not automatic checkout: staff confirm scope, price and any single travel/attendance charge before booking.

New applications follow: details and engagement → encrypted photo upload → preliminary review → confirmed card payment → TrustID capture/liveness or office appointment → staff compliance review and Companies House submission. Existing applications retain their prior payment eligibility and engagement terms. A preliminary review cannot authenticate a document, establish identity, or guarantee acceptance.

## Render configuration

- Required: `DPC_DOCUMENT_REVIEW_KEY`, a Fernet key generated with `Fernet.generate_key()`. Keep it in Render secrets. Never commit it, expose it to browsers, or rotate it while current documents need decryption without a migration.
- Optional: `OPENAI_API_KEY` enables the applicant's explicit opt-in AI review. Without it, uploads enter the staff queue and the website describes team review. ChatGPT subscription access is not API configuration.
- Optional: `DOCUMENT_REVIEW_MODEL` defaults to `gpt-4o-mini`; any replacement must support vision and JSON output. Provider errors fail to staff review without taking payment.
- Existing Stripe, TrustID, email and staff authentication settings remain required. Persist `DB_PATH` on `/var/data`.

Staff open the Documents action, inspect the photographs against the current Companies House standard, and record a reason before approving preliminary suitability or requesting re-upload. Approval is tied to the document version and applicant name, DOB, residency, route and fee. Changed or expired evidence cannot authorise a new checkout. Applicants can copy a private return link; treat it as a credential. Staff enquiries are available through “View bulk / visit enquiries”.

Only clear, current passport images on the remote route may receive automated preliminary clearance. Other documents, permitted expiry exceptions, ambiguous outputs and office routes require staff review. The AI performs no liveness, authenticity or face matching. TrustID is still required for the digital identity check after confirmed payment. Companies House submission remains a professional staff action; the existing TrustID webhook is not represented as automated final approval.

Preliminary images are re-encoded to strip metadata, encrypted in the backend database, restricted to authenticated staff, and expire after seven days. Startup, access and hourly cleanup remove expired active records. Backup retention must be administered separately. Statutory formal verification evidence retained by the business/TrustID has a separate retention requirement; these temporary photographs are not a substitute. API responses are non-cacheable. Upload and AI spending limits apply; monitor the global 30 uploads/day limit before increasing capacity.

The independent-service disclaimer is on all pages. It does not itself establish Google Ads eligibility or replace advertiser certification requirements.

## Validation

Run the tests in `.github/workflows/test.yml`. Tests use temporary databases, synthetic images and mocked payment, AI, TrustID and email responses. They do not charge a real card or submit a real identity document. Live provider credentials and operational staff coverage are deployment dependencies, not proved by mocked tests.
