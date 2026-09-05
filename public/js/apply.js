/* apply.js — client-side wizard logic for /apply.html
   Vanilla JS, no framework. State lives in memory (`state`) for the page session.
   The backend (api_server.py) is the source of truth for the staff dashboard, drives
   the SumUp payment flow, AND sends every staff notification email itself
   (application submitted / payment received / appointment requested) via Brevo's
   transactional email API. This file used to fire those emails client-side via
   FormSubmit.co, which silently dropped every submission whenever its one-time
   "Activate Form" confirmation link hadn't been clicked — that dependency has been
   removed entirely. This script now only calls our own backend endpoints; the
   backend is responsible for notifying info@taxandaccountinghub.com. */

(function () {
  'use strict';

  // Backend API base. '__PORT_8000__' is replaced by deploy_website at deploy
  // time for the local sandbox preview; on the live site it falls back to the
  // permanent Render-hosted backend.
  const API = '__PORT_8000__'.startsWith('__') ? 'https://tah-verify-backend.onrender.com' : '__PORT_8000__';

  const TOTAL_STEPS = 9;
  const form = document.getElementById('wizardForm');
  const steps = Array.from(document.querySelectorAll('.wizard-step'));
  const progressItems = Array.from(document.querySelectorAll('#progressList li'));
  const progressFill = document.getElementById('progressFill');
  const nextBtn = document.querySelector('[data-action="next"]');
  const backBtn = document.querySelector('[data-action="back"]');
  /* ---------------- Pricing table (single source of truth) ----------------
     Price is a function of residency x verification route.
     To change any price, edit ONLY this object. Every display point in the
     wizard (price chip, engagement letter, payment total, review summary)
     and the fee sent to the backend all read from here.
     Restructured 2 September 2026: UK remote tier introduced at £49 to be
     competitive with the free GOV.UK One Login route and low-cost ACSPs;
     £125 retained for the in-person appointment, which is genuinely scarce;
     overseas held at £175, where no free route exists. ------------------- */

  const PRICING = {
    uk: {
      online:      { fee: 49,  label: 'UK-based director — remote check' },
      'in-person': { fee: 125, label: 'UK-based director — in-person appointment' }
    },
    overseas: {
      online:      { fee: 175, label: 'Overseas director — remote check' },
      'in-person': { fee: 175, label: 'Overseas director — in-person appointment' }
    }
  };

  // Lowest and highest advertised prices, derived so copy never goes stale.
  const ALL_FEES = Object.keys(PRICING)
    .reduce((acc, res) => acc.concat(
      Object.keys(PRICING[res]).map((rt) => PRICING[res][rt].fee)
    ), []);
  const FEE_FROM = Math.min.apply(null, ALL_FEES);
  const FEE_TO   = Math.max.apply(null, ALL_FEES);

  const priceChip = document.getElementById('priceChip');
  const priceChipAmount = document.getElementById('priceChipAmount');

  const state = {
    step: 1,
    route: null, // 'online' | 'in-person'
    fee: null,   // set from PRICING: 49 | 125 | 175
    feeLabel: '', // 'UK-based director' | 'Foreign / overseas director'
    signed: false,
    signTimestamp: null,
    signIp: null,
    caseRef: null,
    accessToken: null,
    submitted: false,
    paymentUrl: null,
    paymentCheckoutId: null,
    paymentStatus: 'pending', // 'pending' | 'paid' | 'failed' | 'unavailable'
    appointmentSubmitted: false,
  };

  const OFFICE_ADDRESSES = {
    london: 'Hallings Wharf Studios, 1A Cam Road, London, E15 2SY',
    bedford: '11 Holbeach Avenue, Shortstown, Bedford, MK42 0EG',
  };

  /* ---------------- Navigation ---------------- */

  function showStep(n) {
    steps.forEach((s) => {
      s.classList.toggle('is-active', Number(s.dataset.stepPanel) === n);
    });
    progressItems.forEach((li) => {
      const num = Number(li.dataset.step);
      li.classList.toggle('is-active', num === n);
      li.classList.toggle('is-complete', num < n);
    });
    progressFill.style.width = ((n) / TOTAL_STEPS * 100).toFixed(2) + '%';
    backBtn.hidden = n === 1;
    const isLast = n === TOTAL_STEPS;
    const isSubmitStep = n === 6;
    const isInPersonNextSteps = n === 8 && state.route === 'in-person';
    nextBtn.hidden = isLast || isSubmitStep || isInPersonNextSteps;
    document.querySelector('.wizard-nav').hidden = n === 9 || n === 1;
    priceChip.hidden = n < 2 || n === 9;
    state.step = n;

    if (n === 6) buildReview();
    if (n === 7) initPaymentStep();
    if (n === 8) renderNextStepsPanel();
    if (n === 9) renderDoneStep();

    validateCurrentStep();
    // Move focus to the step heading for accessibility
    const heading = steps[n - 1].querySelector('h2');
    if (heading) heading.setAttribute('tabindex', '-1');
    if (heading) heading.focus({ preventScroll: false });
    window.scrollTo({ top: document.querySelector('.wizard-progress').offsetTop - 8, behavior: 'smooth' });
  }

  function goNext() {
    if (state.step === 5 && !state.signed) return; // must sign before leaving the letter step
    if (state.step === 6) return; // step 6 uses the Submit button, not Next
    if (state.step === 8 && state.route === 'in-person') return; // uses Request appointment button
    if (state.step < TOTAL_STEPS) showStep(state.step + 1);
  }
  function goBack() {
    if (state.step > 1) showStep(state.step - 1);
  }

  document.querySelector('[data-action="begin"]').addEventListener('click', () => showStep(2));
  nextBtn.addEventListener('click', goNext);
  backBtn.addEventListener('click', goBack);

  /* ---------------- Validation per step ---------------- */

  function fieldsFor(step) {
    return Array.from(steps[step - 1].querySelectorAll('input,select,textarea'))
      .filter((el) => el.hasAttribute('required'));
  }

  function isStepValid(step) {
    if (step === 1) return true;
    if (step === 5) return state.signed;
    if (step === 6) return true;
    if (step === 7) return state.paymentStatus === 'paid' || document.getElementById('willPay').checked;
    if (step === 8) return state.route === 'online' ? true : state.appointmentSubmitted;
    if (step === 9) return true;

    const required = fieldsFor(step);
    for (const el of required) {
      if (el.type === 'radio') {
        const group = form.querySelectorAll(`input[name="${el.name}"]`);
        if (!Array.from(group).some((r) => r.checked)) return false;
        continue;
      }
      if (el.type === 'checkbox') {
        if (!el.checked) return false;
        continue;
      }
      if (!el.value || !el.value.trim()) return false;
      if (el.pattern) {
        const re = new RegExp(el.pattern);
        if (!re.test(el.value.trim())) return false;
      }
    }
    return true;
  }

  function validateCurrentStep() {
    nextBtn.disabled = !isStepValid(state.step);
  }

  function markInvalid(el, invalid) {
    el.setAttribute('aria-invalid', invalid ? 'true' : 'false');
  }

  form.addEventListener('input', (e) => {
    if (e.target.matches('input,select,textarea')) {
      markInvalid(e.target, e.target.hasAttribute('required') && !e.target.value);
    }
    validateCurrentStep();
  });
  form.addEventListener('change', () => validateCurrentStep());

  /* ---------------- Step 2: price + route logic ---------------- */

  const residencyRadios = form.querySelectorAll('input[name="residency"]');
  const verifyRouteRadios = form.querySelectorAll('input[name="verifyRoute"]');
  const routeHint = document.getElementById('routeHint');

  function updatePrice() {
    const residency = form.querySelector('input[name="residency"]:checked');
    if (!residency) return;
    const route = form.querySelector('input[name="verifyRoute"]:checked');

    const routes = PRICING[residency.value];
    if (!routes) return;

    if (!route) {
      // Route not chosen yet. Show the entry price for this residency rather
      // than committing to a fee, so the chip is never misleading.
      const fees = Object.keys(routes).map((rt) => routes[rt].fee);
      state.fee = null;
      state.feeLabel = '';
      priceChipAmount.textContent = 'from £' + Math.min.apply(null, fees);
      return;
    }

    const tier = routes[route.value];
    if (!tier) return;

    state.fee = tier.fee;
    state.feeLabel = tier.label;
    priceChipAmount.textContent = '£' + state.fee;
    updateLetterFee();
    updatePaymentTotal();
  }
  residencyRadios.forEach((r) => r.addEventListener('change', updatePrice));

  function updateRoute() {
    const checked = form.querySelector('input[name="verifyRoute"]:checked');
    if (!checked) return;
    state.route = checked.value;
    updatePrice();
    if (state.route === 'online') {
      routeHint.textContent = "You'll complete your ID check remotely, from your own phone or webcam, once we've received your payment. No appointment needed.";
    } else {
      routeHint.textContent = "You'll book an appointment at our Bedford or London (Stratford) office after payment, and bring your original ID document with you.";
    }
    validateCurrentStep();
  }
  verifyRouteRadios.forEach((r) => r.addEventListener('change', updateRoute));

  /* ---------------- Step 3: name composition ---------------- */

  const firstNameInput = document.getElementById('firstName');
  const lastNameInput = document.getElementById('lastName');
  const fullNameHidden = document.getElementById('fullName');
  function updateFullName() {
    fullNameHidden.value = [firstNameInput.value.trim(), lastNameInput.value.trim()].filter(Boolean).join(' ');
  }
  firstNameInput.addEventListener('input', updateFullName);
  lastNameInput.addEventListener('input', updateFullName);

  /* ---------------- Step 3: previous address toggle ---------------- */

  const lessThan12mo = document.getElementById('lessThan12mo');
  const previousAddressField = document.getElementById('previousAddressField');
  const previousAddress = document.getElementById('previousAddress');
  lessThan12mo.addEventListener('change', () => {
    previousAddressField.hidden = !lessThan12mo.checked;
    if (lessThan12mo.checked) {
      previousAddress.setAttribute('required', 'required');
    } else {
      previousAddress.removeAttribute('required');
      previousAddress.value = '';
    }
    validateCurrentStep();
  });

  /* ---------------- Step 5: engagement letter ---------------- */

  const letterFee = document.getElementById('letterFee');
  const letterFeeBasis = document.getElementById('letterFeeBasis');

  function updateLetterFee() {
    if (!state.fee) return;
    letterFee.textContent = '£' + state.fee + ', all-inclusive';
    letterFeeBasis.textContent = state.feeLabel;
  }

  // Auto-fill today's date
  const signDate = document.getElementById('signDate');
  signDate.value = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric' });

  // Attempt to fetch signing IP client-side; degrade gracefully if blocked.
  function fetchSigningIp() {
    fetch('https://api.ipify.org?format=json')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => { if (data && data.ip) state.signIp = data.ip; })
      .catch(() => { state.signIp = null; }); // never blocks the flow
  }
  fetchSigningIp();

  /* ---- Signature pad (canvas draw) ---- */
  const sigPad = document.getElementById('sigPad');
  const sigCtx = sigPad.getContext('2d');
  let drawing = false;
  let hasDrawn = false;

  function resizeCanvasForDPR() {
    const ratio = window.devicePixelRatio || 1;
    const rect = sigPad.getBoundingClientRect();
    if (rect.width === 0) return;
    sigPad.width = rect.width * ratio;
    sigPad.height = 180 * ratio;
    sigCtx.scale(ratio, ratio);
    sigCtx.lineWidth = 2.2;
    sigCtx.lineCap = 'round';
    sigCtx.strokeStyle = '#0A0E12';
  }
  window.addEventListener('load', resizeCanvasForDPR);

  function getPos(evt) {
    const rect = sigPad.getBoundingClientRect();
    const point = evt.touches ? evt.touches[0] : evt;
    return { x: point.clientX - rect.left, y: point.clientY - rect.top };
  }
  function startDraw(evt) {
    drawing = true;
    hasDrawn = true;
    const p = getPos(evt);
    sigCtx.beginPath();
    sigCtx.moveTo(p.x, p.y);
    evt.preventDefault();
  }
  function moveDraw(evt) {
    if (!drawing) return;
    const p = getPos(evt);
    sigCtx.lineTo(p.x, p.y);
    sigCtx.stroke();
    evt.preventDefault();
  }
  function endDraw() { drawing = false; }

  sigPad.addEventListener('mousedown', startDraw);
  sigPad.addEventListener('mousemove', moveDraw);
  window.addEventListener('mouseup', endDraw);
  sigPad.addEventListener('touchstart', startDraw, { passive: false });
  sigPad.addEventListener('touchmove', moveDraw, { passive: false });
  sigPad.addEventListener('touchend', endDraw);

  document.querySelector('[data-action="clear-signature"]').addEventListener('click', () => {
    sigCtx.clearRect(0, 0, sigPad.width, sigPad.height);
    hasDrawn = false;
  });

  const signStatus = document.getElementById('signStatus');
  document.querySelector('[data-action="sign-letter"]').addEventListener('click', () => {
    const name = document.getElementById('signName').value.trim();
    const acks = ['ack1', 'ack2', 'ack3', 'ack4', 'ack5'].map((id) => document.getElementById(id));
    const allChecked = acks.every((a) => a.checked);

    if (!name) {
      signStatus.textContent = 'Please type your full legal name to sign.';
      signStatus.style.color = 'var(--color-error, #e05252)';
      document.getElementById('signName').focus();
      return;
    }
    if (!allChecked) {
      signStatus.textContent = 'Please tick all 5 acknowledgement boxes before signing.';
      signStatus.style.color = 'var(--color-error, #e05252)';
      return;
    }

    if (hasDrawn) {
      document.getElementById('signatureData').value = sigPad.toDataURL('image/png');
    }
    state.signed = true;
    state.signTimestamp = new Date().toISOString();
    signStatus.style.color = '';
    signStatus.textContent = 'Signed on ' + signDate.value + (state.signIp ? ' from IP ' + state.signIp : '') + '. You can continue to review your application.';
    validateCurrentStep();
  });

  /* ---------------- Step 6: review + case ref + submit ---------------- */

  const caseRefDisplay = document.getElementById('caseRefDisplay');
  const payReference = document.getElementById('payReference');
  const reviewSummary = document.getElementById('reviewSummary');
  const submitStatus = document.getElementById('submitStatus');
  const submitBtn = document.getElementById('submitBtn');

  function generateCaseRef() {
    if (state.caseRef) return state.caseRef;
    // Use a cryptographically random suffix (not a timestamp slice) so case
    // references are not practically guessable/brute-forceable by a third
    // party — the reference itself is shown to the applicant and used in
    // emails, so it should not double as a secret with only ~10,000 possible
    // values. Actual access control on the case is handled separately via
    // state.accessToken, issued once by the backend at creation time.
    const bytes = new Uint32Array(2);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    const digits = (bytes[0] % 100000000).toString().padStart(8, '0');
    // Case-reference format is deliberately hyphenated (IDV-YYYY-nnnnnnnn) and
    // must stay that way. It used to be displayed to the applicant with slashes
    // (IDV/2026/nnnnnnnn) while every other system — the database, the Stripe
    // checkout description and metadata, the staff notification emails and the
    // applicant's payment-confirmation email — stored and showed the hyphenated
    // form, because those all received caseRefSlug() rather than the displayed
    // string. An applicant quoting the reference exactly as the site showed it
    // therefore gave staff a value that matched nothing they could search for.
    // One canonical format now flows through display, storage, payment and email.
    //
    // The year is taken from the clock rather than hardcoded: it was previously
    // fixed at '2026', which would have silently stamped every 2027 case with
    // the wrong year.
    state.caseRef = 'IDV-' + new Date().getFullYear() + '-' + digits;
    return state.caseRef;
  }

  // Retained deliberately even though the displayed reference no longer contains
  // slashes: it keeps any reference that was generated by an older cached copy of
  // this script (still sitting in a browser tab mid-application) working against
  // the backend, and it is harmless as a no-op for the current format.
  function caseRefSlug() {
    return (state.caseRef || '').replace(/\//g, '-');
  }

  // This application's access token used to be appended to every request as
  // ?token=..., which wrote it verbatim into the backend host's access logs and
  // into this browser's own history. That token authorises reading and changing
  // this application — including date of birth, home address and the signed
  // engagement letter — so it now travels as a request header, which is not
  // logged that way.
  //
  // Header values cannot carry non-ASCII characters: the browser throws a
  // TypeError and the fetch never leaves. The token is server-generated URL-safe
  // base64 so that should never happen, but an applicant part-way through paying
  // must never be blocked by a defensive assumption, so fall back to the legacy
  // query string in that case. The backend deliberately still accepts both.
  function tokenHeaderSafe(value) {
    return typeof value === 'string' && /^[\x20-\x7E]*$/.test(value);
  }

  function caseFetch(path, options) {
    const opts = Object.assign({}, options || {});
    const token = state.accessToken || '';
    if (tokenHeaderSafe(token)) {
      opts.headers = Object.assign({}, opts.headers || {}, { 'X-Case-Token': token });
      return fetch(API + path, opts);
    }
    const sep = path.indexOf('?') === -1 ? '?' : '&';
    return fetch(API + path + sep + 'token=' + encodeURIComponent(token), opts);
  }

  function buildReview() {
    generateCaseRef();
    caseRefDisplay.textContent = state.caseRef;
    payReference.textContent = 'TAHV-' + state.caseRef;
    payReference.dataset.copyValue = 'TAHV-' + state.caseRef;

    const val = (id) => {
      const el = document.getElementById(id);
      return el ? (el.value || '—') : '—';
    };

    const groups = [];

    groups.push({
      title: 'Your situation',
      rows: [
        ['Role', val('role')],
        ['Service and residency', state.feeLabel || '—'],
        ['Fee', state.fee ? '£' + state.fee + ' (all-inclusive)' : '—'],
        ['Verification route', state.route === 'online' ? 'Online' : state.route === 'in-person' ? 'In-person appointment' : '—'],
      ],
    });

    groups.push({
      title: 'Personal details',
      rows: [
        ['First name(s)', val('firstName')],
        ['Last name', val('lastName')],
        ['Former name(s)', val('formerNames') || 'None'],
        ['Date of birth', val('dob')],
        ['Nationality', val('nationality')],
        ['Country of residence', val('residenceCountry')],
        ['Home address', val('homeAddress')],
        ['At address since', val('addressSince')],
        ['Previous address', lessThan12mo.checked ? val('previousAddress') : 'N/A (12+ months at current address)'],
        ['Email', val('email')],
        ['Mobile', val('mobileCode') + ' ' + val('mobile')],
      ],
    });

    groups.push({
      title: 'Company details',
      rows: [
        ['Company name', val('companyName')],
        ['Company number', val('companyNumber')],
        ['Role (confirmed)', val('roleConfirm')],
      ],
    });

    groups.push({
      title: 'Engagement letter',
      rows: [
        ['Signed', state.signed ? 'Yes' : 'No'],
        ['Typed signature name', val('signName')],
        ['Date', val('signDate')],
        ['Signing IP (audit trail)', state.signIp || 'Not available'],
        ['Fee acknowledged', state.fee ? '£' + state.fee + ', all-inclusive' : '—'],
      ],
    });

    reviewSummary.innerHTML = groups.map((g) => `
      <div class="review-summary__group">
        <h3>${g.title}</h3>
        <dl>
          ${g.rows.map(([k, v]) => `<div class="review-summary__row"><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`).join('')}
        </dl>
      </div>
    `).join('');
  }

  function escapeHtml(str) {
    return str.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Previously this only ever sent 7 fields (case_ref, full_name, first_name,
  // last_name, email, residency, fee_amount, route) even though the wizard
  // above collects ~20: role, former names, DOB, nationality, residence
  // country, home address + how long there (plus previous address if under
  // 12 months), mobile, company name/number, confirmed role, and the signed
  // engagement letter's name/date/IP/signature. All of that was captured on
  // screen and then silently discarded — staff never received it anywhere.
  // Now every field the backend actually has a column for gets sent.
  function applicationPayload() {
    return {
      case_ref: caseRefSlug(),
      full_name: document.getElementById('fullName').value || '',
      first_name: document.getElementById('firstName').value || '',
      last_name: document.getElementById('lastName').value || '',
      email: document.getElementById('email').value || '',
      residency: (form.querySelector('input[name="residency"]:checked') || {}).value || '',
      fee_amount: state.fee || 0,
      route: state.route || '',
      role: val('role'),
      former_names: val('formerNames'),
      dob: val('dob'),
      nationality: val('nationality'),
      residence_country: val('residenceCountry'),
      home_address: val('homeAddress'),
      address_since: val('addressSince'),
      previous_address: val('previousAddress'),
      mobile: (val('mobileCode') + ' ' + val('mobile')).trim(),
      company_name: val('companyName'),
      company_number: val('companyNumber'),
      role_confirm: val('roleConfirm'),
      sign_name: val('signName'),
      sign_date: val('signDate'),
      sign_ip: state.signIp || '',
      signature_data: val('signatureData'),
    };
  }


  function val(id) {
    const el = document.getElementById(id);
    return el ? (el.value || '') : '';
  }

  submitBtn.addEventListener('click', async () => {
    if (state.submitted) return;
    generateCaseRef();
    submitBtn.disabled = true;
    submitStatus.textContent = 'Submitting your application securely…';
    submitStatus.classList.remove('is-error');

    try {
      const createRes = await fetch(API + '/api/applications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(applicationPayload()),
      });
      const createData = await createRes.json();
      // The backend hands back a one-time access token for this case at
      // creation — required on every later call for this case_ref so that
      // guessing/enumerating case references alone can't read or modify
      // someone else's application.
      state.accessToken = createData.access_token;
      // The backend marks the application submitted AND sends the staff
      // notification email itself (server-side, via Brevo) — that's the
      // reliable notification channel; there is no separate third-party form
      // service in this flow, so applicant PII isn't shared with anyone
      // beyond this backend.
      await caseFetch('/api/applications/' + encodeURIComponent(caseRefSlug()) + '/submitted', { method: 'POST' });

      state.submitted = true;
      submitStatus.textContent = 'Application submitted. Taking you to payment…';
      showStep(7);
    } catch (err) {
      submitStatus.textContent = "We couldn't submit your application just now — please check your connection and try again, or email info@taxandaccountinghub.com directly quoting case " + state.caseRef + ".";
      submitStatus.classList.add('is-error');
      submitBtn.disabled = false;
    }
  });

  /* ---------------- Step 7: payment ---------------- */

  const payOnlineStatus = document.getElementById('payOnlineStatus');
  const payOnlineBtn = document.getElementById('payOnlineBtn');
  const checkPaymentBtn = document.getElementById('checkPaymentBtn');
  const payOnlinePaidBadge = document.getElementById('payOnlinePaidBadge');
  const willPay = document.getElementById('willPay');
  let paymentPollTimer = null;

  function setPayStatus(text, isError) {
    payOnlineStatus.textContent = text;
    payOnlineStatus.classList.toggle('is-error', !!isError);
  }

  async function initPaymentStep() {
    if (state.paymentStatus === 'paid') {
      showPaidState();
      return;
    }
    if (state.paymentUrl) {
      showPayLink();
      startPaymentPolling();
      return;
    }
    setPayStatus('Preparing your secure card payment link…');
    payOnlineBtn.hidden = true;
    checkPaymentBtn.hidden = true;
    try {
      const res = await caseFetch('/api/applications/' + encodeURIComponent(caseRefSlug()) + '/payment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin: window.location.origin }),
      });
      if (!res.ok) throw new Error('payment link request failed (' + res.status + ')');
      const data = await res.json();
      state.paymentUrl = data.hosted_checkout_url;
      state.paymentCheckoutId = data.checkout_id;
      showPayLink();
      startPaymentPolling();
    } catch (err) {
      setPayStatus('Online card payment is temporarily unavailable, please use the bank transfer details below instead.', true);
    }
  }

  function showPayLink() {
    setPayStatus('Your secure card payment link is ready.');
    payOnlineBtn.href = state.paymentUrl;
    payOnlineBtn.hidden = false;
    checkPaymentBtn.hidden = false;
    payOnlinePaidBadge.hidden = true;
  }

  function showPaidState() {
    setPayStatus('');
    payOnlineBtn.hidden = true;
    checkPaymentBtn.hidden = true;
    payOnlinePaidBadge.hidden = false;
    stopPaymentPolling();
    validateCurrentStep();
  }

  function startPaymentPolling() {
    stopPaymentPolling();
    paymentPollTimer = setInterval(checkPaymentStatus, 6000);
  }
  function stopPaymentPolling() {
    if (paymentPollTimer) { clearInterval(paymentPollTimer); paymentPollTimer = null; }
  }

  async function checkPaymentStatus() {
    try {
      const res = await caseFetch('/api/applications/' + encodeURIComponent(caseRefSlug()) + '/payment-status');
      const data = await res.json();
      state.paymentStatus = data.payment_status;
      if (data.payment_status === 'paid') {
        showPaidState();
        // The backend detects the pending -> paid transition itself (this same
        // /payment-status call triggers it) and sends the "Payment received"
        // staff notification server-side — no client-side email call needed.
        return true;
      }
      return false;
    } catch (err) {
      return false;
    }
  }

  checkPaymentBtn.addEventListener('click', async () => {
    checkPaymentBtn.disabled = true;
    checkPaymentBtn.textContent = 'Checking…';
    const paid = await checkPaymentStatus();
    if (!paid) {
      setPayStatus('Payment not received yet. If you have just paid, this can take a minute to confirm.');
    }
    checkPaymentBtn.disabled = false;
    checkPaymentBtn.textContent = "I've paid — check status";
  });

  willPay.addEventListener('change', validateCurrentStep);

  const payTotal = document.getElementById('payTotal');
  function updatePaymentTotal() {
    if (!state.fee) return;
    payTotal.textContent = '£' + state.fee;
  }

  // Copy-to-clipboard buttons
  document.querySelectorAll('[data-copy-btn]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const valueEl = btn.previousElementSibling;
      const text = valueEl.dataset.copyValue || valueEl.textContent;
      const done = () => {
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        btn.classList.add('is-copied');
        setTimeout(() => { btn.textContent = original; btn.classList.remove('is-copied'); }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
      } else {
        fallbackCopy(text, done);
      }
    });
  });
  function fallbackCopy(text, cb) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    document.body.removeChild(ta);
    cb();
  }

  /* ---------------- Step 8: next steps (route-conditional) ---------------- */

  const nextStepsOnline = document.getElementById('nextStepsOnline');
  const nextStepsInPerson = document.getElementById('nextStepsInPerson');
  const appointmentOffice = document.getElementById('appointmentOffice');
  const appointmentOfficeHelp = document.getElementById('appointmentOfficeHelp');
  const appointmentDate = document.getElementById('appointmentDate');
  const appointmentTimePref = document.getElementById('appointmentTimePref');
  const appointmentStatus = document.getElementById('appointmentStatus');
  const submitAppointmentBtn = document.getElementById('submitAppointmentBtn');

  appointmentDate.min = new Date().toISOString().slice(0, 10);

  appointmentOffice.addEventListener('change', () => {
    const addr = OFFICE_ADDRESSES[appointmentOffice.value];
    appointmentOfficeHelp.textContent = addr ? 'Address: ' + addr : '';
  });

  function renderNextStepsPanel() {
    nextStepsOnline.hidden = state.route !== 'online';
    nextStepsInPerson.hidden = state.route !== 'in-person';
    nextBtn.hidden = state.step === 8 && state.route === 'in-person';
  }

  submitAppointmentBtn.addEventListener('click', async () => {
    if (!appointmentOffice.value) {
      appointmentStatus.textContent = 'Please select an office.';
      appointmentStatus.classList.add('is-error');
      appointmentOffice.focus();
      return;
    }
    if (!appointmentDate.value) {
      appointmentStatus.textContent = 'Please select a preferred date.';
      appointmentStatus.classList.add('is-error');
      appointmentDate.focus();
      return;
    }
    if (!appointmentTimePref.value) {
      appointmentStatus.textContent = 'Please select a preferred time.';
      appointmentStatus.classList.add('is-error');
      appointmentTimePref.focus();
      return;
    }

    submitAppointmentBtn.disabled = true;
    appointmentStatus.classList.remove('is-error');
    appointmentStatus.textContent = 'Sending your appointment request…';

    try {
      await caseFetch('/api/applications/' + encodeURIComponent(caseRefSlug()) + '/appointment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          appointment_office: appointmentOffice.value,
          appointment_date: appointmentDate.value,
          appointment_time_pref: appointmentTimePref.value,
        }),
      });
      // The backend records the appointment AND sends the staff notification
      // email itself (server-side, via Brevo) — no client-side email call needed.

      state.appointmentSubmitted = true;
      appointmentStatus.textContent = 'Appointment request sent.';
      showStep(9);
    } catch (err) {
      appointmentStatus.textContent = "We couldn't send your appointment request just now — please try again, or email info@taxandaccountinghub.com directly quoting case " + state.caseRef + ".";
      appointmentStatus.classList.add('is-error');
      submitAppointmentBtn.disabled = false;
    }
  });

  /* ---------------- Step 9: confirmation ---------------- */

  function renderDoneStep() {
    document.getElementById('confirmCaseRef').textContent = state.caseRef || '—';
    const msg = document.getElementById('confirmRouteMessage');
    const paidByCard = state.paymentStatus === 'paid';
    if (state.route === 'online') {
      msg.textContent = paidByCard
        ? "Thank you — your card payment is confirmed and your application is with our team. We'll email you a secure link to complete your online identity check within 1 working day."
        : "Thank you — your application is with our team. Once your bank transfer clears (using the reference shown on the payment step), we'll email you a secure link to complete your online identity check within 1 working day.";
    } else {
      msg.textContent = paidByCard
        ? "Thank you — your card payment is confirmed and your appointment request has been sent. A member of our team will confirm your exact slot by phone or email within 1 working day."
        : "Thank you — your appointment request has been sent. Once your bank transfer clears (using the reference shown on the payment step), a member of our team will confirm your exact slot by phone or email within 1 working day.";
    }
  }

  /* ---------------- jsPDF: downloadable signed engagement letter ---------------- */

  function generateLetterPdf() {
    if (!window.jspdf) {
      alert('The PDF library did not load. Please check your connection and try again.');
      return;
    }
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ unit: 'pt', format: 'a4' });
    const margin = 48;
    const pageWidth = doc.internal.pageSize.getWidth();
    const maxWidth = pageWidth - margin * 2;
    let y = margin;

    function addLine(text, opts = {}) {
      const size = opts.size || 10;
      const style = opts.style || 'normal';
      doc.setFont('helvetica', style);
      doc.setFontSize(size);
      const lines = doc.splitTextToSize(text, maxWidth);
      lines.forEach((line) => {
        if (y > doc.internal.pageSize.getHeight() - margin) {
          doc.addPage();
          y = margin;
        }
        doc.text(line, margin, y);
        y += size * 1.35;
      });
      y += 4;
    }

    addLine('Engagement Letter — ACSP Identity Verification (Individuals)', { size: 15, style: 'bold' });
    addLine('Tax And Accounting Hub Ltd — Company No. 08408126, 11 Holbeach Avenue, Shortstown, Bedford, MK42 0EG', { size: 9 });
    y += 6;

    const feeText = state.fee ? ('£' + state.fee + ', all-inclusive (' + state.feeLabel + ')') : ('£' + FEE_FROM + ' to £' + FEE_TO + ' depending on residency and verification route, all-inclusive');
    const routeText = state.route === 'in-person'
      ? 'you will attend a booked appointment at our Bedford or London (Stratford) office, where a member of staff will personally examine your original identity document'
      : 'once payment is received we will email you a secure one-time link to our identity verification partner, TrustID, to complete a remote check from your own phone or webcam';

    const bodySections = [
      ['1. Purpose and scope of this engagement', 'You have asked Tax And Accounting Hub Ltd to act as your Authorised Corporate Service Provider (ACSP) to verify your identity with Companies House under the Economic Crime and Corporate Transparency Act 2023 and the Registrar\u2019s (Identity Verification by ACSPs) Rules. Tax And Accounting Hub Ltd will collect your data, facilitate your chosen verification route, submit an Identity Verification Statement to Companies House, confirm the outcome in writing, and retain records as required by law.'],
      ['2. Fee', 'Our fee for this service is ' + feeText + '. Payment is due after you submit this application and before we open your identity verification link or confirm an appointment, and is collected securely online.'],
      ['3. How your identity will be verified', 'You do not need to upload any documents through this application. Instead, ' + routeText + '. If you are not resident in the United Kingdom, your identity document must be government-issued. All documents must be seen in their original form.'],
      ['4. Your responsibilities', 'You confirm all information provided is true, accurate and complete; you will complete the identity check personally within 14 days of receiving your verification link (online route); you will provide an email address only you can access; and you will notify Tax And Accounting Hub Ltd of any relevant change in circumstances.'],
      ['5. Data protection', 'Tax And Accounting Hub Ltd is the data controller. The lawful basis is performance of a legal obligation under UK corporate transparency and AML law; biometric data is processed under the substantial public interest condition of the Data Protection Act 2018. TrustID is used as sub-processor for digital checks. Companies House receives only name, DOB, address, email and document reference details — not document copies.'],
      ['6. Record retention', 'Records relating to this engagement, including failed attempts, are retained for 7 years from completion, then securely destroyed.'],
      ['7. Liability, confidentiality and termination', 'Liability is capped at the fee you paid (£' + FEE_FROM + ' to £' + FEE_TO + ', as applicable to your case), except where it cannot lawfully be limited. Information is kept confidential save where disclosure is legally required (e.g. AML reporting, which cannot be notified to you). Either party may terminate by written notice; fees for completed work remain payable. Governed by the law of England and Wales.'],
      ['8. What this engagement does not cover', 'Incorporating a company, appointing directors, other Companies House filings, connecting your personal code to your Companies House record, verifying any other individual, Register of Overseas Entities verification, or tax/legal/immigration advice.'],
      ['9. Complaints', 'Contact Kader Ameen, Director, at Kader@taxandaccountinghub.com. Complaints are acknowledged within 5 working days and fully responded to within 20 working days; unresolved complaints may be escalated to the AAT.'],
    ];

    bodySections.forEach(([title, body]) => {
      addLine(title, { size: 11, style: 'bold' });
      addLine(body, { size: 9.5 });
    });

    y += 6;
    addLine('Signature', { size: 11, style: 'bold' });
    addLine('Typed name: ' + (document.getElementById('signName').value || '—'), { size: 9.5 });
    addLine('Date: ' + (signDate.value || '—'), { size: 9.5 });
    addLine('Case reference: ' + (state.caseRef || 'Not yet generated'), { size: 9.5 });
    if (state.signIp) addLine('Signing IP (audit trail): ' + state.signIp, { size: 8.5 });

    if (hasDrawn) {
      try {
        const imgData = sigPad.toDataURL('image/png');
        if (y > doc.internal.pageSize.getHeight() - 140) { doc.addPage(); y = margin; }
        addLine('Drawn signature:', { size: 9.5, style: 'italic' });
        doc.addImage(imgData, 'PNG', margin, y, 220, 66);
        y += 76;
      } catch (e) { /* canvas may be tainted in rare cases; skip image gracefully */ }
    }

    doc.save('Director-Personal-Code-Engagement-Letter-' + (state.caseRef || 'draft') + '.pdf');
  }

  document.querySelector('[data-action="download-pdf"]').addEventListener('click', generateLetterPdf);

  /* ---------------- Init ---------------- */
  showStep(1);
})();
