(function () {
  'use strict';
  const form = document.getElementById('enquiryForm');
  if (!form) return;
  const status = document.getElementById('enquiryStatus');
  const estimate = document.getElementById('enquiryEstimate');
  function totals() {
    if (!estimate) return;
    const u = Number(form.elements.uk_count?.value || 0), o = Number(form.elements.overseas_count?.value || 0);
    estimate.textContent = form.elements.service.value === 'bulk'
      ? (u + o < 10 ? 'Minimum 10 applicants in total.' : 'Indicative standard remote total: £' + (u * 39 + o * 85).toLocaleString('en-GB') + '. Scope and any larger-volume pricing will be confirmed by email.')
      : 'We will agree a quote before booking.';
  }
  form.addEventListener('input', totals); totals();
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    button.disabled = true; status.textContent = 'Saving your enquiry…';
    try {
      const data = Object.fromEntries(new FormData(form));
      data.uk_count = Number(data.uk_count || 0); data.overseas_count = Number(data.overseas_count || 0);
      const response = await fetch('https://tah-verify-backend.onrender.com/api/enquiries', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Please check your entries.');
      status.textContent = result.message + ' Reference: ' + result.reference + (result.notification_sent ? '' : ' Email notification was delayed. Please call or email us quoting this reference.');
      try { window.DPCAnalytics?.event('generate_lead', {}, true); } catch (_) {}
      button.textContent = 'Enquiry received';
    } catch (error) {
      status.textContent = 'Your enquiry could not be confirmed. ' + error.message + ' You can also call +44 7914 393183 or email info@taxandaccountinghub.com.';
      button.disabled = false;
    }
  });
})();
