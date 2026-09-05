/* mobile-nav.js — toggles the mobile navigation drawer */
(function () {
  const btn = document.querySelector('[data-menu-toggle]');
  const menu = document.querySelector('[data-mobile-nav]');
  if (!btn || !menu) return;
  btn.addEventListener('click', () => {
    const isOpen = menu.classList.toggle('is-open');
    btn.setAttribute('aria-expanded', String(isOpen));
  });
})();
