/* theme.js — dark/light mode toggle. No localStorage (sandboxed iframes block it);
   defaults to system preference, in-memory state for the session. */
(function () {
  const root = document.documentElement;
  const toggle = document.querySelector('[data-theme-toggle]');
  let mode = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  root.setAttribute('data-theme', mode);

  function iconFor(mode) {
    return mode === 'dark'
      ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M2 12h2.5M19.5 12H22M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></svg>'
      : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.5 14.2A8.5 8.5 0 1 1 9.8 3.5a7 7 0 0 0 10.7 10.7z"/></svg>';
  }

  if (toggle) {
    toggle.innerHTML = iconFor(mode);
    toggle.setAttribute('aria-label', 'Switch to ' + (mode === 'dark' ? 'light' : 'dark') + ' mode');
    toggle.addEventListener('click', () => {
      mode = mode === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', mode);
      toggle.innerHTML = iconFor(mode);
      toggle.setAttribute('aria-label', 'Switch to ' + (mode === 'dark' ? 'light' : 'dark') + ' mode');
    });
  }
})();
