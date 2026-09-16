/* Basic consent mode: Google is contacted only after analytics opt-in.
   Never send form fields, case references, access tokens, query strings or hashes. */
(function () {
  'use strict';
  const id = window.DPC_GA4_ID;
  const enabled = /^G-[A-Z0-9]+$/.test(id || '') && location.hostname === 'directorpersonalcode.uk';
  const key = 'dpc_analytics_consent_v1';
  let allowed = false, loaded = false;
  const once = new Set();
  const events = new Set(['page_view', 'application_start', 'application_step', 'application_submitted', 'begin_checkout', 'payment_confirmed', 'generate_lead', 'contact_click', 'application_error']);
  function choice() {
    try { const v = JSON.parse(localStorage.getItem(key)); return v && v.expires > Date.now() ? v.value : null; } catch (_) { return null; }
  }
  function cleanContext() {
    let referrer = '';
    try { referrer = new URL(document.referrer).origin; } catch (_) {}
    return {page_location: location.origin + location.pathname, page_referrer: referrer, page_title: document.title};
  }
  function event(name, params, unique) {
    if (!enabled || !allowed || !events.has(name)) return;
    const safe = cleanContext();
    // Explicit allowlist: arbitrary input data can never enter the payload.
    if (params && Number.isInteger(params.step) && params.step >= 1 && params.step <= 9) safe.step = params.step;
    if (params && ['phone', 'email', 'enquiry', 'apply'].includes(params.contact_method)) safe.contact_method = params.contact_method;
    if (params && ['submission', 'checkout'].includes(params.error_stage)) safe.error_stage = params.error_stage;
    const token = name + ':' + (safe.step || '');
    if (unique && once.has(token)) return;
    if (unique) once.add(token);
    window.gtag('event', name, Object.assign(safe, {send_to: id}));
  }
  function start() {
    if (!enabled || loaded || !allowed) return;
    loaded = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag('consent', 'default', {analytics_storage: 'granted', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied'});
    window.gtag('js', new Date());
    const config = Object.assign(cleanContext(), {send_page_view: false, allow_google_signals: false, allow_ad_personalization_signals: false, cookie_expires: 60 * 60 * 24 * 90});
    // Only our controlled campaign values, never arbitrary incoming query values.
    const query = new URLSearchParams(location.search);
    if (query.get('utm_source') === 'google' && query.get('utm_medium') === 'organic' && /^gbp_/.test(query.get('utm_campaign') || '')) {
      config.campaign_source = 'google'; config.campaign_medium = 'organic'; config.campaign_name = 'gbp_dpc';
    }
    window.gtag('config', id, config);
    event('page_view', {}, true);
    const script = document.createElement('script');
    script.async = true; script.src = 'https://www.googletagmanager.com/gtag/js?id=' + id;
    document.head.appendChild(script);
  }
  function clearCookies() {
    document.cookie.split(';').forEach(item => {
      const name = item.trim().split('=')[0];
      if (!/^_ga(?:_|$)/.test(name)) return;
      ['', '; Domain=' + location.hostname, '; Domain=.' + location.hostname].forEach(domain => {
        document.cookie = name + '=; Max-Age=0; Path=/' + domain + '; SameSite=Lax; Secure';
      });
    });
  }
  function setChoice(value) {
    try { localStorage.setItem(key, JSON.stringify({value, expires: Date.now() + 180 * 86400000})); } catch (_) {}
    allowed = value === 'granted';
    window['ga-disable-' + id] = !allowed;
    if (!allowed) { clearCookies(); if (loaded) location.reload(); }
    else start();
    document.querySelector('.cookie-banner')?.remove();
  }
  function banner() {
    if (document.querySelector('.cookie-banner')) return;
    const el = document.createElement('div');
    el.className = 'cookie-banner is-visible'; el.setAttribute('role', 'region'); el.setAttribute('aria-label', 'Analytics preference');
    el.innerHTML = '<p>With your permission, we use Google Analytics to understand visits and improve our application process. We do not send your application details to Analytics. <a href="/privacy#cookies">Privacy and cookies</a></p><div class="analytics-actions"><button type="button" class="btn btn-primary btn-sm" data-choice="denied">Reject optional</button><button type="button" class="btn btn-primary btn-sm" data-choice="granted">Accept analytics</button></div>';
    el.querySelectorAll('[data-choice]').forEach(b => b.addEventListener('click', () => setChoice(b.dataset.choice)));
    document.body.appendChild(el);
  }
  window.DPCAnalytics = {event};
  if (!enabled || /^\/(staff|qa)(\/|\.|$)/.test(location.pathname)) return;
  allowed = choice() === 'granted';
  function init() {
    const footer = document.querySelector('footer') || document.body;
    const settings = document.createElement('button'); settings.type = 'button'; settings.className = 'btn btn-sm'; settings.textContent = 'Cookie settings'; settings.addEventListener('click', banner); footer.appendChild(settings);
    if (choice() === null) banner();
    start();
    document.addEventListener('click', e => {
      const a = e.target.closest('a[href]'); if (!a) return;
      const href = a.getAttribute('href');
      if (href.startsWith('tel:')) event('contact_click', {contact_method: 'phone'});
      else if (href.startsWith('mailto:')) event('contact_click', {contact_method: 'email'});
      else if (/^\/contact(?:\.html)?(?:#|$)/.test(href)) event('contact_click', {contact_method: 'enquiry'});
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
