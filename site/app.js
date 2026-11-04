'use strict';

const menuButton = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#navigation');
function closeMenu() {
  menuButton.setAttribute('aria-expanded', 'false');
  menuButton.setAttribute('aria-label', '打开导航');
  navigation.classList.remove('is-open');
}
menuButton.addEventListener('click', () => {
  const open = menuButton.getAttribute('aria-expanded') !== 'true';
  menuButton.setAttribute('aria-expanded', String(open));
  menuButton.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
  navigation.classList.toggle('is-open', open);
});
navigation.addEventListener('click', event => {
  if (event.target.closest('a')) closeMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && menuButton.getAttribute('aria-expanded') === 'true') {
    closeMenu();
    menuButton.focus();
  }
});
window.matchMedia('(min-width: 561px)').addEventListener('change', closeMenu);

const copyButton = document.querySelector('#copy-prompt');
const copyStatus = document.querySelector('#copy-status');
copyButton.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(document.querySelector('#example-prompt').textContent);
    copyButton.textContent = '已复制 ✓';
    copyStatus.textContent = '示例任务已复制，可以粘贴到 Codex。';
    setTimeout(() => { copyButton.textContent = '复制 ⧉'; }, 2500);
  } catch {
    copyStatus.textContent = '复制未成功，请选中下方示例文字复制。';
    copyButton.textContent = '请手动复制';
  }
});
