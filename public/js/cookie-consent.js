/* cookie-consent.js — simple, dismissible, essential-only cookie notice.
   No dark patterns: a single "Got it" acknowledgement. No tracking cookies are set
   by this site.

   The acknowledgement is remembered for 12 months so the notice appears once, not
   on every page. Storage preference order:
     1. localStorage  — no cookie needed at all.
     2. a first-party "dpc_cookie_ack" cookie — fallback when localStorage is
        unavailable (Safari private mode, blocked storage). This is itself a
        strictly necessary cookie: it exists only to honour the user's dismissal.
   If neither is writable the notice degrades to the previous per-page behaviour. */
(function () {
  const KEY = 'dpc_cookie_ack';
  const VALUE = '1';
  const MAX_AGE = 60 * 60 * 24 * 365; // 12 months

  function readCookie() {
    return document.cookie.split(';').some(function (c) {
      return c.trim().indexOf(KEY + '=') === 0;
    });
  }

  function acknowledged() {
    try {
      if (window.localStorage && localStorage.getItem(KEY) === VALUE) return true;
    } catch (e) {
      /* storage blocked — fall through to cookie check */
    }
    return readCookie();
  }

  function remember() {
    let stored = false;
    try {
      if (window.localStorage) {
        localStorage.setItem(KEY, VALUE);
        stored = true;
      }
    } catch (e) {
      stored = false;
    }
    if (stored) return;
    try {
      const secure = location.protocol === 'https:' ? '; Secure' : '';
      document.cookie =
        KEY + '=' + VALUE + '; Path=/; Max-Age=' + MAX_AGE + '; SameSite=Lax' + secure;
    } catch (e) {
      /* nothing else we can do; notice will show again next page */
    }
  }

  function init() {
    if (acknowledged()) return;
    if (document.querySelector('.cookie-banner')) return;

    const banner = document.createElement('div');
    banner.className = 'cookie-banner';
    banner.setAttribute('role', 'region');
    banner.setAttribute('aria-label', 'Cookie notice');
    banner.innerHTML =
      '<p>We use only essential cookies needed to run this site and application form &mdash; no tracking or advertising cookies. <a href="/privacy.html#cookies">Learn more</a>.</p>' +
      '<button type="button" class="btn btn-primary btn-sm cookie-banner__dismiss">Got it</button>';
    document.body.appendChild(banner);

    requestAnimationFrame(() => banner.classList.add('is-visible'));

    banner.querySelector('.cookie-banner__dismiss').addEventListener('click', () => {
      remember();
      banner.classList.remove('is-visible');
      setTimeout(() => banner.remove(), 300);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
