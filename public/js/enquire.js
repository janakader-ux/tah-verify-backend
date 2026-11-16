(function () {
  'use strict';
  const form = document.getElementById('enquiryForm');
  if (!form) return;
  const status = document.getElementById('enquiryStatus');
  const estimate = document.getElementById('enquiryEstimate');
  const formType = location.pathname.replace(/\.html$/, '').replace(/\/$/, '') === '/contact' ? 'contact' : 'specialist_enquiry';
  function track(name, params, unique) {
    try { window.DPCAnalytics?.event(name, Object.assign({form_type: formType}, params), unique); } catch (_) {}
  }
  function trackStart() { track('enquiry_start', {}, true); }
  function trackInteraction(e) {
    // Ignore hidden fields and the honeypot; never send any field values.
    if (e.target && e.target.name !== 'website' && e.target.type !== 'hidden') trackStart();
  }
  form.addEventListener('input', trackInteraction);
  form.addEventListener('change', trackInteraction);
  form.addEventListener('invalid', () => {
    trackStart();
    track('enquiry_error', {error_stage: 'validation'}, true);
  }, true);
  function totals() {
    if (!estimate) return;
    const u = Number(form.elements.uk_count?.value || 0), o = Number(form.elements.overseas_count?.value || 0);
    estimate.textContent = form.elements.service.value === 'bulk'
      ? (u + o === 0 ? 'Enter the number of applicants.' : 'Indicative remote total: £' + (u * (u >= 25 ? 39 : 49) + o * (o >= 10 ? 85 : 119)).toLocaleString('en-GB') + '. £39 applies to 25+ UK/EU chip passports, £85 to 10+ overseas applicants; smaller numbers are at the standard price. Final pricing confirmed by email.')
      : 'We will agree a quote before booking.';
  }
  form.addEventListener('input', totals); totals();
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    trackStart();
    button.disabled = true; status.textContent = 'Saving your enquiry…';
    let failureStage = 'network';
    try {
      const data = Object.fromEntries(new FormData(form));
      data.uk_count = Number(data.uk_count || 0); data.overseas_count = Number(data.overseas_count || 0);
      const response = await fetch('https://tah-verify-backend.onrender.com/api/enquiries', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      failureStage = response.ok ? 'response' : 'server';
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Please check your entries.');
      status.textContent = result.message + ' Reference: ' + result.reference + (result.notification_sent ? '' : ' Email notification was delayed. Please call or email us quoting this reference.');
      track('generate_lead', {}, true);
      button.textContent = 'Enquiry received';
    } catch (error) {
      track('enquiry_error', {error_stage: failureStage}, true);
      status.textContent = 'Your enquiry could not be confirmed. ' + error.message + ' You can also call +44 7914 393183 or email info@taxandaccountinghub.com.';
      button.disabled = false;
    }
  });
})();
