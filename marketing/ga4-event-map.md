# Director Personal Code GA4 event map

Property: Director Personal Code - GA4 (554710155). Web stream G-F8YKTG54MX.

All browser analytics require opt-in. New event payloads contain only allowlisted route, step and error-stage values, with page URL stripped of query/hash.

## Application

application_step: step 1 Start; 2 Situation; 3 Personal details; 4 Company; 5 Engagement letter; 6 Review; 7 Payment; 8 Next steps; 9 Done.
Each screen is counted once per page lifecycle after consent. Granting consent later reports only the current screen, without replaying earlier activity.

## Enquiries

enquiry_start: first input/change or validation/submission attempt, once per page after consent.
enquiry_error: once per error stage/form/page; stages validation, network, server, response.
generate_lead: accepted backend response, once per page. Delayed notification email remains an accepted enquiry, not a submission error.
form_type: contact or specialist_enquiry, derived from the page path.

## GA4 definitions

Application step: Event scope, parameter step.
Enquiry form type: Event scope, parameter form_type.
Error stage: Event scope, parameter error_stage.

Start/error events are diagnostics, not key events. Keep actual accepted enquiry/application/payment outcomes separate; do not sum them into unique leads.

## Checks

node --test scripts/tests/test_frontend.cjs scripts/tests/test_analytics.cjs scripts/tests/test_enquiry_analytics.cjs

Tests use synthetic in-memory data and mocked network responses; they do not send enquiry notifications or trigger payments.
