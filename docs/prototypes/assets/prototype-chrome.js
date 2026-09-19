(() => {
  const chrome = document.querySelector('[data-prototype-chrome]');
  if (!chrome) return;
  const hotspotToggle = chrome.querySelector('[data-hotspot-toggle]');
  const resetButton = chrome.querySelector('[data-prototype-reset]');
  let idleTimer = null;
  const setHotspotsVisible = (visible) => {
    document.body.classList.toggle('hotspots-on', visible);
    if (!hotspotToggle) return;
    hotspotToggle.setAttribute('aria-pressed', String(visible));
    hotspotToggle.textContent = `◎ 可点击区域：${visible ? '开' : '关'}`;
  };
  const wake = () => { window.clearTimeout(idleTimer); chrome.classList.remove('is-idle'); };
  const sleepLater = (delay = 2500) => {
    window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => {
      if (!chrome.matches(':hover') && !chrome.matches(':focus-within')) chrome.classList.add('is-idle');
    }, delay);
  };
  hotspotToggle?.addEventListener('click', () => setHotspotsVisible(!document.body.classList.contains('hotspots-on')));
  resetButton?.addEventListener('click', () => { setHotspotsVisible(true); document.dispatchEvent(new CustomEvent('prototype:reset')); });
  chrome.addEventListener('pointerenter', wake);
  chrome.addEventListener('pointerleave', () => sleepLater(1200));
  chrome.addEventListener('focusin', wake);
  chrome.addEventListener('focusout', () => sleepLater(1200));
  setHotspotsVisible(true);
  sleepLater();
})();
