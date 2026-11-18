(() => {
  'use strict';

  const key = 'chemellm-theme';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const root = document.documentElement;
  const browserColor = document.querySelector('meta[name="theme-color"]');
  let preference = null;
  let themeButtons = [];

  function applyTheme() {
    const theme = preference ?? (system.matches ? 'dark' : 'light');
    root.dataset.theme = theme;
    browserColor?.setAttribute('content', theme === 'dark' ? '#111111' : '#fafafa');
    for (const button of themeButtons) {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
    }
  }

  try {
    const saved = localStorage.getItem(key);
    if (saved === 'light' || saved === 'dark') preference = saved;
  } catch {
    // Browsers that block storage can still switch themes for the current page.
  }
  applyTheme();

  document.addEventListener('DOMContentLoaded', () => {
    const themeSwitch = document.querySelector('.theme-switch');
    if (!themeSwitch) return;
    themeButtons = themeSwitch.querySelectorAll('button');
    applyTheme();
    themeSwitch.hidden = false;
    for (const button of themeButtons) {
      button.addEventListener('click', () => {
        preference = button.dataset.themeChoice;
        applyTheme();
        try {
          localStorage.setItem(key, preference);
        } catch {
          // Keep the current page usable when the browser blocks storage.
        }
      });
    }
  });

  system.addEventListener('change', applyTheme);
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = event.newValue === 'light' || event.newValue === 'dark' ? event.newValue : null;
    applyTheme();
  });
})();
