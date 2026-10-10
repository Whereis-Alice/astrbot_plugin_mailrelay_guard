// 通用展示原语。所有动态值都经过 esc()。保留历史导出，供其他模块与测试复用。
import { paths } from './icons.js';

export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Resolve the logo relative to this module so it works without any DOM, in the
// AstrBot page bridge and in the import-time test harness alike.
export const logoUrl = new URL('./assets/logo.svg', import.meta.url).href;

// 邮件图标：内联 SVG，随信纸主题变量着色，不发任何网络请求（规避面板里的资源 404）。
export const logoMark = (prefix = 'mm', decorative = false) =>
  `<svg class="brand-logo" viewBox="0 0 256 256" ${decorative ? 'aria-hidden="true"' : 'role="img" aria-label="我的邮件"'} xmlns="http://www.w3.org/2000/svg">`
  + `<defs><linearGradient id="${prefix}-bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color:var(--accent)"></stop><stop offset="1" style="stop-color:var(--accent-strong)"></stop></linearGradient></defs>`
  + `<rect width="256" height="256" rx="60" fill="url(#${prefix}-bg)"></rect>`
  + `<circle cx="196" cy="54" r="58" style="fill:var(--accent-contrast)" opacity=".10"></circle>`
  + `<rect x="46" y="80" width="164" height="100" rx="22" style="fill:var(--accent-contrast)"></rect>`
  + `<path d="M66 100 128 146 190 100" fill="none" style="stroke:var(--accent)" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"></path>`
  + `<path d="M62 164 106 128" fill="none" style="stroke:var(--accent)" stroke-width="7" stroke-linecap="round" opacity=".25"></path>`
  + `<path d="M194 164 150 128" fill="none" style="stroke:var(--accent)" stroke-width="7" stroke-linecap="round" opacity=".25"></path>`
  + `</svg>`;

export function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.mail}</svg>`;
}
export function fillIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
}

export const statusNames = {submitted:'SMTP 已接受',partial:'部分接受',failed:'发送失败',unknown:'状态未知',sending:'发送中',accepted:'已接受',refused:'已拒绝',pending:'等待中'};
export const badge = (text, kind = '') => `<span class="badge ${esc(kind)}">${esc(text)}</span>`;
export const statusBadge = (status) => badge(statusNames[status] || status || '未知', ['failed','refused'].includes(status) ? 'bad' : ['partial','unknown','sending'].includes(status) ? 'warn' : '');

export function date(value, short = false) {
  if (!value) return '尚无记录';
  const d = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(d.getTime()) ? '时间未知' : d.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...(short ? {} : {year:'numeric',second:'2-digit'})});
}

export const heading = (name, desc, actions = '') => `<div class="page-heading"><div class="page-heading-text"><div class="eyebrow">MY MAIL / WORKSPACE</div><h1>${esc(name)}</h1><p>${esc(desc)}</p></div>${actions ? `<div class="page-heading-actions">${actions}</div>` : ''}</div>`;
export const empty = (title, text, symbol = 'mail') => `<div class="empty">${icon(symbol)}<h2>${esc(title)}</h2><p>${esc(text)}</p></div>`;
export const loading = (text = '正在加载…') => `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;
export const notice = (text, kind = '') => `<div class="notice ${esc(kind)}">${esc(text)}</div>`;
export const button = (action, text, kind = 'secondary', symbol = '') => `<button type="button" class="button ${esc(kind)}" data-action="${esc(action)}">${symbol ? icon(symbol) : ''}${esc(text)}</button>`;
export const row = (label, value) => `<div class="meta-row"><span>${esc(label)}</span><span>${esc(value)}</span></div>`;

// Card primitive shared across every view.
export const card = (title, desc, body, extra = '') => `<section class="card"><div class="card-head"><div class="card-head-text"><h2>${esc(title)}</h2>${desc ? `<p>${esc(desc)}</p>` : ''}</div>${extra}</div><div class="card-body">${body}</div></section>`;

export function field(settings, name, label, help = '', type = 'text', opts = {}) {
  const value = settings[name];
  const id = `field-${name}`;
  if (type === 'boolean') return `<div class="switch-row"><label for="${id}"><strong>${esc(label)}</strong><small>${esc(help)}</small></label><span class="switch"><input id="${id}" name="${esc(name)}" type="checkbox" ${value ? 'checked' : ''}><span class="switch-track" aria-hidden="true"></span></span></div>`;
  const multiline = ['list', 'template'].includes(type);
  const input = multiline
    ? `<textarea id="${id}" name="${esc(name)}" data-type="${type}" class="${type === 'template' ? 'code-input' : ''}" rows="${type === 'template' ? 10 : 3}" spellcheck="false">${esc(type === 'list' ? (value || []).join('\n') : value)}</textarea>`
    : `<input id="${id}" name="${esc(name)}" type="${type}" value="${esc(value)}" ${opts.min !== undefined ? `min="${opts.min}" max="${opts.max}" step="1" required` : ''}>`;
  return `<label class="field ${opts.full ? 'full' : ''}" for="${id}"><span class="field-label">${esc(label)}</span>${input}<small>${esc(help)}</small></label>`;
}

export function safeFrame(html, title) {
  // Template previews use an opaque-origin sandbox plus a restrictive CSP. No
  // scripts, navigation, network images, forms or embedded content are allowed.
  const src = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; form-action 'none'; base-uri 'none'"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;overflow-wrap:anywhere;font-family:system-ui,sans-serif}img{max-width:100%}a{pointer-events:none}</style></head><body>${html}</body></html>`;
  return `<iframe class="preview-frame" sandbox="" referrerpolicy="no-referrer" title="${esc(title)}" srcdoc="${esc(src)}"></iframe>`;
}
