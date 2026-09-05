/* reveal.js — subtle scroll-reveal via IntersectionObserver.
   Progressive enhancement: elements are visible by default (see CSS).
   Only once JS has confirmed IntersectionObserver works do we "arm" an
   element (opacity:0 + clip) and immediately start observing it, so a
   slow/broken observer can never leave content permanently hidden. */
(function () {
  if (!('IntersectionObserver' in window)) return;
  const items = document.querySelectorAll('[data-reveal]');
  if (!items.length) return;

  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-revealed');
          io.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: '0px 0px -8% 0px' }
  );

  items.forEach((el) => {
    const rect = el.getBoundingClientRect();
    const alreadyVisible = rect.top < window.innerHeight * 0.92 && rect.bottom > 0;
    if (alreadyVisible) return; // never arm elements already on screen at load
    el.classList.add('reveal-armed');
    io.observe(el);
  });

  // Safety net: if anything armed never gets revealed within 4s (e.g. a
  // stuck observer in unusual embed contexts), force it visible.
  window.setTimeout(() => {
    document.querySelectorAll('[data-reveal].reveal-armed:not(.is-revealed)').forEach((el) => {
      el.classList.add('is-revealed');
    });
  }, 4000);
})();
