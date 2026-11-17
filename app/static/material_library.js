// Native GET submission stays available when JavaScript is disabled.
(() => {
  const form = document.querySelector('.materials-source');
  if (!form) return;
  const source = form.querySelector('[name=account]');
  const hint = form.querySelector('[data-filter-hint]');
  if (!source || !hint) return;
  hint.textContent = '切换来源后自动筛选，也可点击应用筛选。';
  source.addEventListener('change', () => form.requestSubmit());
})();
