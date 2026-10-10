// Shared presentation primitives. Dynamic values always pass through esc().
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export const logoUrl = document.querySelector('.brand img').src;
const paths = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  mail:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 6 9 7 9-7"/>',
  pulse:'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  sliders:'<path d="M4 6h4m4 0h8M4 12h10m4 0h2M4 18h2m4 0h10"/><circle cx="10" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
  refresh:'<path d="M20 7a9 9 0 1 0 1 8M20 3v5h-5"/>',
  moon:'<path d="M20 15a9 9 0 0 1-11-11 9 9 0 1 0 11 11Z"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  alert:'<path d="M12 3 2 20h20Z"/><path d="M12 9v4m0 3v.1"/>',
  expand:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  star:'<path d="m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z"/>',
};
export function icon(name) { return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.mail}</svg>`; }
export function fillIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
export const statusNames = {submitted:"SMTP 已接受",partial:"部分接受",failed:"发送失败",unknown:"状态未知",sending:"发送中",accepted:"已接受",refused:"已拒绝",pending:"等待中"};
export const badge = (text, kind = "") => `<span class="badge ${esc(kind)}">${esc(text)}</span>`;
export const statusBadge = (status) => badge(statusNames[status] || status || "未知", ["failed","refused"].includes(status) ? "bad" : ["partial","unknown","sending"].includes(status) ? "warn" : "");
export function date(value, short = false) {
  if (!value) return "尚无记录";
  const d = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(d.getTime()) ? "时间未知" : d.toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",...(short ? {} : {year:"numeric",second:"2-digit"})});
}
export const heading = (name, desc, actions = "") => `<div class="page-heading"><div><div class="eyebrow">MY MAIL / WORKSPACE</div><h1>${esc(name)}</h1><p>${esc(desc)}</p></div>${actions}</div>`;
export const empty = (title, text, symbol = "mail") => `<div class="empty">${icon(symbol)}<h2>${esc(title)}</h2><p>${esc(text)}</p></div>`;
export const loading = () => '<div class="loading"><span class="spinner"></span>正在加载…</div>';
export const notice = (text, kind = "") => `<div class="notice ${esc(kind)}">${esc(text)}</div>`;
export const button = (action, text, kind = "secondary", symbol = "") => `<button type="button" class="button ${esc(kind)}" data-action="${esc(action)}">${symbol ? icon(symbol) : ""}${esc(text)}</button>`;
export const row = (label, value) => `<div class="meta-row"><span>${esc(label)}</span><span>${esc(value)}</span></div>`;
export function field(settings, name, label, help = "", type = "text", opts = {}) {
  const value = settings[name];
  const id = `field-${name}`;
  if (type === "boolean") return `<div class="switch-row"><label for="${id}"><strong>${esc(label)}</strong><small>${esc(help)}</small></label><input id="${id}" name="${esc(name)}" type="checkbox" ${value ? "checked" : ""}></div>`;
  const multiline = ["list", "template"].includes(type);
  const input = multiline ? `<textarea id="${id}" name="${esc(name)}" data-type="${type}" class="${type === "template" ? "code-input" : ""}" rows="${type === "template" ? 10 : 3}" spellcheck="false">${esc(type === "list" ? (value || []).join("\n") : value)}</textarea>` : `<input id="${id}" name="${esc(name)}" type="${type}" value="${esc(value)}" ${opts.min !== undefined ? `min="${opts.min}" max="${opts.max}" step="1" required` : ""}>`;
  return `<label class="field ${opts.full ? "full" : ""}" for="${id}"><span>${esc(label)}</span>${input}<small>${esc(help)}</small></label>`;
}
export function safeFrame(html, title) {
  // Template previews use an opaque-origin sandbox plus a restrictive CSP. No
  // scripts, navigation, network images, forms or embedded content are allowed.
  const src = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; form-action 'none'; base-uri 'none'"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;overflow-wrap:anywhere;font-family:system-ui,sans-serif}img{max-width:100%}a{pointer-events:none}</style></head><body>${html}</body></html>`;
  return `<iframe class="preview-frame" sandbox="" referrerpolicy="no-referrer" title="${esc(title)}" srcdoc="${esc(src)}"></iframe>`;
}
