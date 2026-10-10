// App shell: rail navigation, topbar, theme picker, toast and confirm dialog.
// Builds the whole chrome in JS so the layout lives in one place. Views render
// into #view; the shell stays mounted for the session.
import { $, $$, on, delegate, group } from './dom.js';
import { icon, logoUrl, esc } from './ui.js';
import { THEMES, apply as applyTheme, resolveInitial, schemeOf, toggleScheme } from './theme.js';

export const ROUTES = [
  { id:'overview', title:'运行概览', icon:'grid',    hint:'邮件、投递与连接的全景' },
  { id:'mailbox',  title:'邮件中心', icon:'mail',    hint:'记录、阅读与本地副本' },
  { id:'monitor',  title:'掉线守护', icon:'pulse',   hint:'QQ 掉线提醒与趋势' },
  { id:'settings', title:'投递设置', icon:'sliders', hint:'投递能力与内容保护' },
];
const TITLES = Object.fromEntries(ROUTES.map(r => [r.id, r.title]));

let toastTimer;
export function toast(message) {
  const el = $('#toast'); if (!el) return;
  el.textContent = message; el.hidden = false; el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.classList.remove('show'); el.hidden = true; }, 5500);
}

export function confirmDialog(title, text) {
  const dialog = $('#confirm-dialog');
  if (!dialog || dialog.open) return Promise.resolve(false);
  $('#confirm-title').textContent = title;
  $('#confirm-text').textContent = text;
  dialog.returnValue = 'cancel';
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }));
}

const themeGroup = (scheme, label) => {
  const items = THEMES.filter(t => t.scheme === scheme).map(t =>
    `<button type="button" class="theme-option" role="menuitemradio" data-theme="${t.id}" aria-checked="false">
      <span class="theme-swatch" aria-hidden="true">${t.swatch.map(c => `<i style="background:${esc(c)}"></i>`).join('')}</span>
      <span class="theme-option-name">${esc(t.name)}</span>
      <span class="theme-check" aria-hidden="true">${icon('check')}</span>
    </button>`).join('');
  return `<div class="theme-group"><div class="theme-group-label">${icon(scheme === 'dark' ? 'moon' : 'sun')}${esc(label)}</div><div class="theme-options" role="group">${items}</div></div>`;
};

function shellMarkup() {
  const nav = ROUTES.map(r =>
    `<a class="rail-link" href="#${r.id}" data-nav="${r.id}"><span class="rail-ico">${icon(r.icon)}</span><span class="rail-text"><strong>${esc(r.title)}</strong><small>${esc(r.hint)}</small></span></a>`).join('');
  return `
  <div class="app-shell">
    <div class="rail-scrim" id="rail-scrim" hidden></div>
    <aside class="rail" id="rail" aria-label="主导航">
      <a class="rail-brand" href="#overview">
        <img src="${esc(logoUrl)}" alt="" width="40" height="40">
        <span class="rail-brand-text"><strong>我的邮件</strong><small>每一封，都有着落</small></span>
      </a>
      <nav class="rail-nav">${nav}</nav>
      <div class="rail-foot">
        <div class="rail-card"><span class="pulse-dot" aria-hidden="true"></span><div><strong>邮件与连接</strong><small>一处安静地照看</small></div></div>
        <button type="button" class="theme-trigger" id="theme-trigger" aria-haspopup="true" aria-expanded="false">
          <span class="theme-trigger-swatch" id="theme-trigger-swatch" aria-hidden="true"></span>
          <span class="theme-trigger-text"><strong id="theme-trigger-name">主题</strong><small>切换信纸风格</small></span>
          <span class="theme-trigger-ico" aria-hidden="true">${icon('palette')}</span>
        </button>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <button class="icon-button menu-toggle" id="menu-toggle" type="button" aria-label="打开导航" aria-expanded="false" aria-controls="rail">${icon('menu')}</button>
          <div class="breadcrumb"><span class="crumb-root">我的邮件</span><span class="crumb-sep" aria-hidden="true">/</span><strong id="breadcrumb-title">运行概览</strong></div>
        </div>
        <div class="topbar-actions">
          <span class="conn-badge" id="connection-state"><span class="conn-dot"></span><span id="connection-text">连接中</span></span>
          <button class="icon-button" id="scheme-toggle" type="button" aria-label="快速切换深浅外观">${icon('moon')}</button>
          <button class="icon-button" id="refresh" type="button" aria-label="刷新当前页面">${icon('refresh')}</button>
        </div>
      </header>
      <div id="demo-banner" class="demo-banner" hidden><span class="demo-dot"></span>当前是演示预览，展示示例数据。测试与保存只影响本页，不会发送真实邮件。</div>
      <main id="view" tabindex="-1" aria-busy="true"><div class="loading"><span class="spinner"></span>正在打开邮件工作空间…</div></main>
      <footer class="footer"><span>我的邮件</span><span class="footer-sep">·</span><span>温柔提醒，安心投递</span></footer>
    </div>
  </div>
  <div class="theme-popover" id="theme-popover" role="menu" aria-label="选择主题" hidden>
    <div class="theme-popover-head">邮件主题</div>
    ${themeGroup('light', '浅色信纸')}
    ${themeGroup('dark', '深色信笺')}
  </div>
  <div id="toast" class="toast" role="status" aria-live="polite" hidden></div>
  <dialog id="confirm-dialog" class="confirm-dialog">
    <form method="dialog">
      <h2 id="confirm-title">请确认</h2>
      <p id="confirm-text"></p>
      <div class="button-row"><button value="cancel" class="button secondary">取消</button><button value="confirm" class="button danger">确认</button></div>
    </form>
  </dialog>`;
}

function paintTheme(current) {
  const theme = THEMES.find(t => t.id === current);
  const swatch = $('#theme-trigger-swatch'), name = $('#theme-trigger-name');
  if (theme && swatch) swatch.style.background = theme.swatch[0];
  if (theme && name) name.textContent = theme.name;
  $$('.theme-option').forEach(el => el.setAttribute('aria-checked', String(el.dataset.theme === current)));
  const scheme = schemeOf(current);
  const toggle = $('#scheme-toggle');
  if (toggle) { toggle.innerHTML = icon(scheme === 'dark' ? 'sun' : 'moon'); toggle.setAttribute('aria-label', scheme === 'dark' ? '切换浅色外观' : '切换深色外观'); }
}

export function setRoute(route) {
  $('#breadcrumb-title') && ($('#breadcrumb-title').textContent = TITLES[route] || '运行概览');
  $$('[data-nav]').forEach(el => {
    const active = el.dataset.nav === route;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
}

export function setConnection(demo) {
  const badge = $('#connection-state'), text = $('#connection-text'), banner = $('#demo-banner');
  if (text) text.textContent = demo ? '演示预览' : '已连接 AstrBot';
  if (badge) badge.classList.toggle('demo', !!demo);
  if (banner) banner.hidden = !demo;
}
export function setConnectionFailed() {
  const text = $('#connection-text'), badge = $('#connection-state');
  if (text) text.textContent = '连接失败';
  if (badge) badge.classList.add('failed');
}

function closeRail() { $('#rail')?.classList.remove('open'); $('#rail-scrim') && ($('#rail-scrim').hidden = true); $('#menu-toggle')?.setAttribute('aria-expanded', 'false'); }
function openRail() { $('#rail')?.classList.add('open'); $('#rail-scrim') && ($('#rail-scrim').hidden = false); $('#menu-toggle')?.setAttribute('aria-expanded', 'true'); }

// Renders the chrome and wires persistent controls. hooks: { onRefresh, canLeave }.
export function renderShell(hooks = {}) {
  const appEl = $('#app');
  appEl.innerHTML = shellMarkup();

  const popover = $('#theme-popover'), trigger = $('#theme-trigger');
  const closePopover = () => { popover.hidden = true; trigger.setAttribute('aria-expanded', 'false'); };
  const openPopover = () => {
    const rect = trigger.getBoundingClientRect();
    popover.hidden = false;
    popover.style.left = Math.max(14, rect.left) + 'px';
    popover.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
    trigger.setAttribute('aria-expanded', 'true');
    popover.querySelector('.theme-option[aria-checked="true"]')?.focus();
  };

  on(trigger, 'click', () => { popover.hidden ? openPopover() : closePopover(); });
  delegate(popover, 'click', '.theme-option', (_e, el) => { paintTheme(applyTheme(el.dataset.theme)); closePopover(); });
  on(document, 'click', e => { if (!popover.hidden && !e.target.closest('#theme-popover,#theme-trigger')) closePopover(); });
  on(document, 'keydown', e => { if (e.key === 'Escape' && !popover.hidden) { closePopover(); trigger.focus(); } });

  on($('#scheme-toggle'), 'click', () => paintTheme(applyTheme(toggleScheme(document.documentElement.getAttribute('data-mail-theme')))));

  on($('#menu-toggle'), 'click', () => { $('#rail').classList.contains('open') ? closeRail() : openRail(); });
  on($('#rail-scrim'), 'click', closeRail);
  delegate($('#rail'), 'click', '[data-nav],.rail-brand', () => closeRail());

  on($('#refresh'), 'click', async () => {
    if (hooks.canLeave && !(await hooks.canLeave())) { toast('当前有未保存的修改，请先保存再刷新。'); return; }
    hooks.onRefresh && hooks.onRefresh();
  });

  paintTheme(document.documentElement.getAttribute('data-mail-theme') || resolveInitial());
  return { closeRail };
}

export const titleOf = route => TITLES[route] || '运行概览';
