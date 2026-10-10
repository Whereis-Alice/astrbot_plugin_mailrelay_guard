import * as api from './api.js';
import {esc,fillIcons,loading,empty,notice,button,safeFrame} from './ui.js';
import {overview,mailbox,messageList,reader,monitor,settingsView} from './views.js';

const main = document.getElementById('main');
const titles = {overview:'运行概览',mailbox:'邮件中心',monitor:'掉线守护',settings:'投递设置'};
const state = {route:'overview',folder:'sent',query:'',status:'',format:'',offset:0,items:[],selected:null,detail:null,tab:'plain',focused:false,dirty:false};
let routeEpoch = 0, listEpoch = 0, detailEpoch = 0, toastTimer, settingsSnapshot = {}, monitorTimer;
const $ = selector => document.querySelector(selector);
function toast(message) { const el = $('#toast'); el.textContent = message; el.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; },5500); }
function errorBlock(error) { return empty('暂时无法加载',error.message || '请求失败，请稍后重试。','alert')+`<div class="button-row" style="justify-content:center">${button('retry','重新加载')}</div>`; }
async function confirmAction(title, text) {
  const dialog = $('#confirm-dialog'); $('#confirm-title').textContent = title; $('#confirm-text').textContent = text;
  if (dialog.open) return false;
  dialog.returnValue = 'cancel'; dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close',() => resolve(dialog.returnValue === 'confirm'),{once:true}));
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('#theme-toggle span:last-child').textContent = theme === 'dark' ? '切换浅色外观' : '切换深色外观';
  try { localStorage.setItem('my-mail-theme',theme); } catch { /* Restricted iframe storage. */ }
}
let savedTheme;
try { savedTheme = localStorage.getItem('my-mail-theme'); } catch { /* System theme below. */ }
applyTheme(savedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
fillIcons();
$('#demo-banner').hidden = !api.demo;
$('#connection-state').textContent = api.demo ? '演示预览' : '已连接 AstrBot';
$('#theme-toggle').addEventListener('click',() => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
$('#menu-toggle').addEventListener('click',() => { const open = $('#sidebar').classList.toggle('open'); $('#menu-toggle').setAttribute('aria-expanded',String(open)); });
document.addEventListener('click',event => { if (!event.target.closest('#sidebar,#menu-toggle')) { $('#sidebar').classList.remove('open'); $('#menu-toggle').setAttribute('aria-expanded','false'); } });

async function renderRoute() {
  const requested = location.hash.slice(1).split('?')[0], route = titles[requested] ? requested : 'overview';
  if (state.dirty && route !== state.route) {
    if (!await confirmAction('离开当前设置？','当前有尚未保存的修改。离开后这些修改不会保留。')) { history.replaceState(null,'','#'+state.route); return; }
  }
  const epoch = ++routeEpoch; ++listEpoch; ++detailEpoch;
  clearTimeout(monitorTimer); state.route = route; state.dirty = false; state.focused = false;
  $('#breadcrumb-title').textContent = titles[route];
  document.querySelectorAll('[data-nav]').forEach(el => { const active = el.dataset.nav === route; el.classList.toggle('active',active); if (active) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current'); });
  $('#sidebar').classList.remove('open'); $('#menu-toggle').setAttribute('aria-expanded','false');
  main.innerHTML = loading(); main.setAttribute('aria-busy','true');
  try {
    if (route === 'overview') {
      const [summary,messages] = await Promise.all([api.get('webui/summary'),api.get('webui/messages',{folder:'sent',limit:5})]);
      if (epoch !== routeEpoch) return;
      main.innerHTML = overview(summary,messages.items);
    } else if (route === 'mailbox') {
      main.innerHTML = mailbox(state); await loadMessages();
    } else {
      const payload = await api.get('webui/settings');
      const off = route === 'monitor' ? await api.get('webui/offline-status') : null;
      if (epoch !== routeEpoch) return;
      settingsSnapshot = payload.settings;
      main.innerHTML = route === 'monitor' ? monitor(settingsSnapshot,off) : settingsView(settingsSnapshot);
      if (route === 'monitor') scheduleMonitorRefresh(epoch);
    }
    if (epoch === routeEpoch) main.setAttribute('aria-busy','false');
  } catch(error) { if (epoch === routeEpoch) { main.innerHTML = errorBlock(error); main.setAttribute('aria-busy','false'); } }
}
function scheduleMonitorRefresh(epoch) {
  // Refresh only status data. Rebuilding the form would erase unsaved edits.
  monitorTimer = setTimeout(async () => {
    if (state.route !== 'monitor' || epoch !== routeEpoch || document.hidden) { if (epoch === routeEpoch) scheduleMonitorRefresh(epoch); return; }
    try {
      const off = await api.get('webui/offline-status');
      if (epoch !== routeEpoch) return;
      const probe = document.createElement('div'); probe.innerHTML = monitor(settingsSnapshot,off);
      const current = main.querySelector('.grid-two'); current?.replaceWith(probe.querySelector('.grid-two'));
    } catch { /* Explicit refresh will surface errors without disrupting editing. */ }
    if (epoch === routeEpoch) scheduleMonitorRefresh(epoch);
  },15000);
}
async function loadMessages() {
  const epoch = ++listEpoch; const el = $('#mail-list'); if (!el) return;
  ++detailEpoch; state.selected = null; state.detail = null;
  $('#reader').classList.remove('focus-mode'); $('#reader').innerHTML = empty('选一封邮件，慢慢读','点击列表，查看内容和投递结果。'); state.focused = false;
  el.innerHTML = loading();
  try {
    const result = await api.get('webui/messages',{folder:state.folder,query:state.query,status:state.status,format:state.format,limit:20,offset:state.offset});
    if (epoch !== listEpoch || state.route !== 'mailbox') return;
    state.items = result.items || []; el.innerHTML = messageList(result,'',state);
  } catch(error) { if (epoch === listEpoch) el.innerHTML = errorBlock(error); }
}
async function selectItem(index) {
  const item = state.items[index]; if (!item) return;
  const epoch = ++detailEpoch; state.selected = item; state.detail = null;
  main.querySelectorAll('[data-item]').forEach(el => { const active = Number(el.dataset.item) === index; el.classList.toggle('active',active); el.setAttribute('aria-pressed',String(active)); });
  $('#reader').innerHTML = loading();
  try {
    const detail = await api.get('webui/message/'+item.id);
    if (epoch !== detailEpoch || state.route !== 'mailbox') return;
    state.detail = detail; state.tab = detail.html_preview ? 'html' : 'plain'; renderReader();
    if (matchMedia('(max-width:760px)').matches) $('#reader').scrollIntoView({behavior:'smooth',block:'start'});
  } catch(error) { if (epoch === detailEpoch) $('#reader').innerHTML = empty('暂时无法阅读',error.message,'alert'); }
}
function renderReader() { if (state.detail) { $('#reader').innerHTML = reader(state.detail,state.selected,state.tab,state.focused); $('#reader').classList.toggle('focus-mode',state.focused); } }
function formValues(form) {
  const values = {};
  form.querySelectorAll('[name]').forEach(el => { values[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.dataset.type === 'list' ? el.value.split(/[\n,;]+/).map(item => item.trim()).filter(Boolean) : el.value; });
  return values;
}
async function save(form) {
  if (!form.reportValidity()) return;
  const values = formValues(form), changed = Object.fromEntries(Object.entries(values).filter(([key,value]) => JSON.stringify(value) !== JSON.stringify(settingsSnapshot[key])));
  if (!Object.keys(changed).length) { state.dirty = false; toast('设置没有变化。'); return; }
  const submit = form.querySelector('[type=submit]'); submit.disabled = true;
  const epoch = routeEpoch;
  try {
    const result = await api.post('webui/settings',{settings:changed});
    if (epoch !== routeEpoch) { toast('设置已保存。'); return; }
    settingsSnapshot = result.settings.settings; state.dirty = false;
    toast(result.restart_required ? '设置已保存。HTML 工具开关需要重载插件。' : '设置已保存，已立即生效。');
    await renderRoute();
  } catch(error) { toast(error.message); }
  finally { submit.disabled = false; }
}
main.addEventListener('input',event => {
  if (event.target.closest('.settings-form')) { state.dirty = true; if ($('#save-status')) $('#save-status').textContent = '有未保存的修改'; }
});
main.addEventListener('change',event => {
  if (event.target.closest('.settings-form')) { state.dirty = true; if ($('#save-status')) $('#save-status').textContent = '有未保存的修改'; }
  if (event.target.id === 'status-filter' || event.target.id === 'format-filter') { state[event.target.id === 'status-filter' ? 'status' : 'format'] = event.target.value; state.offset = 0; void loadMessages(); }
});
main.addEventListener('submit',event => {
  event.preventDefault();
  if (event.target.id === 'search-form') { state.query = new FormData(event.target).get('query').trim(); state.offset = 0; void loadMessages(); }
  else void save(event.target);
});
async function act(action) {
  if (action === 'retry') { await api.ready(); await renderRoute(); }
  if (action === 'smtp-test') toast((await api.post('webui/smtp-probe')).message);
  if (action === 'offline-test') {
    if (state.dirty) { toast('请先保存修改，再发送测试通知。'); return; }
    if (await confirmAction('发送掉线测试通知？',api.demo ? '当前是演示预览，只展示测试结果，不会发送真实邮件。' : '会向已配置的通知邮箱发送一封真实测试邮件。未配置通知邮箱时，使用主人邮箱。')) toast((await api.post('webui/offline-test')).message);
  }
  if (action === 'template-preview') {
    const values = formValues($('#monitor-form')), variables = {nickname:'我的机器人',bot_id:'10001',platform:'aiocqhttp',time:new Date().toLocaleString('zh-CN'),reason:'OneBot 客户端连接已断开',downtime:'3分20秒'};
    const render = template => template.replace(/\{\{\s*(\w+)\s*\}\}/g,(match,key) => key in variables ? esc(variables[key]) : match);
    $('#template-preview').hidden = false;
    $('#template-preview').innerHTML = `<h3>掉线通知</h3>${safeFrame(render(values.offline_body_template),'掉线通知模板')}<h3 style="margin-top:20px">恢复通知</h3>${safeFrame(render(values.recovery_body_template),'恢复通知模板')}`;
  }
  if (action === 'clear-history' && await confirmAction('清空本地邮件记录？','这会永久删除本插件的本地历史和投递副本，邮箱服务商中的邮件不受影响。')) {
    const result = await api.post('webui/history-clear',{confirm:'clear-mail-history'}); toast(`已清空 ${result.removed} 条本地记录。`);
  }
  if (action === 'focus-reader') { state.focused = !state.focused; renderReader(); }
  if (['mark-read','star','archive'].includes(action)) {
    const selected = state.selected, recipient = selected?.recipient; if (!recipient) return;
    const values = action === 'mark-read' ? {is_read:!recipient.is_read} : action === 'star' ? {is_starred:!recipient.is_starred} : {archived:true};
    await api.post('webui/mailbox-state',{message_id:selected.id,recipient_token:recipient.token,...values});
    Object.assign(recipient,values); renderReader(); if (action === 'archive') await loadMessages(); else { const item = main.querySelector('.message-button.active .message-bottom'); if (item && action === 'star') { const star = item.querySelector('[aria-label="已收藏"]'); if (values.is_starred && !star) item.insertAdjacentHTML('beforeend','<span aria-label="已收藏">★</span>'); else if (!values.is_starred) star?.remove(); } }
  }
}
main.addEventListener('click',async event => {
  const target = event.target.closest('button'); if (!target || target.disabled) return;
  if (target.dataset.folder) { state.folder = target.dataset.folder; state.offset = 0; main.querySelectorAll('[data-folder]').forEach(el => { const active = el === target; el.classList.toggle('active',active); el.setAttribute('aria-pressed',String(active)); }); await loadMessages(); }
  if (target.dataset.page) { state.offset = Math.max(0,state.offset+(target.dataset.page === 'next' ? 20 : -20)); await loadMessages(); }
  if (target.dataset.item !== undefined) await selectItem(Number(target.dataset.item));
  if (target.dataset.readerTab) { state.tab = target.dataset.readerTab; renderReader(); }
  if (target.dataset.action) {
    target.disabled = true;
    try { await act(target.dataset.action); } catch(error) { toast(error.message || '操作失败'); }
    finally { target.disabled = false; }
  }
});
$('#refresh').addEventListener('click',async () => { if (state.dirty) { toast('当前有未保存的修改，请先保存再刷新。'); return; } try { await api.ready(); await renderRoute(); } catch(error) { toast(error.message); } });
window.addEventListener('hashchange',() => { void renderRoute(); });
window.addEventListener('beforeunload',event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('keydown',event => { if (event.key === 'Escape' && state.focused) { state.focused = false; renderReader(); } });
try { await api.ready(); await renderRoute(); }
catch(error) { main.innerHTML = notice(error.message,'error')+button('retry','重新连接'); main.setAttribute('aria-busy','false'); $('#connection-state').textContent = '连接失败'; }
