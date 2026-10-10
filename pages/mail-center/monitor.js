// 掉线守护：守护状态 + 通知方式 + 掉线概览（折线/时间轴/事件）+ 守护设置 + 通知模板。
// 15 秒后台刷新只更新状态，不触碰未保存的表单。纯前端重做，后端契约不变。
import { esc, icon, badge, date, heading, card, notice, button, row, field, safeFrame } from './ui.js';
import { $, on, delegate, bindActions, group } from './dom.js';
import { toast, confirmDialog } from './shell.js';
import { historyShell, historyContent, historyError, bindHistory } from './offline-charts.js';
import { saveBar, formValues, saveSettings } from './forms.js';

const REFRESH_MS = 15000;
const PREVIEW_VARS = { nickname:'我的机器人', bot_id:'10001', platform:'aiocqhttp', time:new Date().toLocaleString('zh-CN'), reason:'OneBot 客户端连接已断开', downtime:'3分20秒' };
const fillVars = template => String(template || '').replace(/\{\{\s*(\w+)\s*\}\}/g, (m, key) => key in PREVIEW_VARS ? esc(PREVIEW_VARS[key]) : m);
const safePreview = (template, title) => safeFrame(fillVars(template), title);

function monitorStates(off) {
  const monitored = off.monitored || [];
  if (!monitored.length) {
    return `<div class="empty">${icon('pulse')}<h2>${off.enabled ? '等待检测 QQ 连接' : '掉线守护尚未开启'}</h2><p>${off.enabled ? '检测结果将在下一轮出现，请确认 AstrBot 已启用 OneBot 平台。' : '开启守护后，这里会显示受监控账号的连接状态。'}</p></div>`;
  }
  return monitored.map(item => {
    const label = item.probe_state === 'unknown' ? '待确认' : item.online ? (item.fail_count ? '检测异常' : '在线') : '已离线';
    const kind = item.probe_state === 'unknown' ? 'warn' : item.online ? (item.fail_count ? 'warn' : 'ok') : 'bad';
    return `<div class="monitor-card ${item.online ? '' : 'offline'}"><div class="monitor-avatar">${icon('pulse')}</div><div class="monitor-body"><strong>${esc(item.nickname || item.bot_id || 'QQ 机器人')}</strong><small>${esc(item.platform)} · ${esc(item.bot_id || item.key)}</small>${item.fail_count ? `<p class="monitor-note">连续失败 ${esc(item.fail_count)} 次${item.downtime ? ' · 掉线 ' + esc(item.downtime) : ''}</p>` : ''}${item.last_reason ? `<p class="monitor-note">${esc(item.last_reason)}</p>` : ''}${item.notification_pending ? '<p class="monitor-note">通知邮件未完全提交，正在限次重试。</p>' : ''}</div>${badge(label, kind)}</div>`;
  }).join('');
}

// 守护状态卡 + 通知方式卡（15 秒刷新只替换这一块）。
function statusGrid(settings, off) {
  const guard = card('正在守护的连接', '适用于 NapCat / LLOneBot 的 OneBot 反向 WebSocket 连接。',
    monitorStates(off) + `<p class="monitor-note">上次检测：${esc(date(off.last_check_at))}</p>`,
    off.enabled ? badge('守护开启', 'ok') : badge('守护关闭', 'neutral'));
  const guardCol = `<div class="stack">${guard}${off.last_error ? notice(off.last_error, 'error') : ''}</div>`;
  const way = card('通知方式', 'QQ 本身掉线时，通过邮箱通知你。',
    row('有效收件人', `${off.effective_recipients?.count || 0} 位`) +
    row('收件人来源', off.effective_recipients?.source === 'custom' ? '单独配置的通知邮箱' : '主人邮箱（未单独配置时）') +
    row('判断条件', `每 ${settings.offline_check_interval_seconds} 秒检测，连续失败 ${settings.offline_failure_threshold} 次`) +
    row('恢复通知', settings.offline_notify_on_recovery ? '已开启' : '已关闭'));
  return `<div class="grid-two monitor-status">${guardCol}<div class="stack">${way}</div></div>`;
}

function markup(settings, off) {
  const f = (name, label, help, type, opts) => field(settings, name, label, help, type, opts);
  const guardCard = card('守护设置', '保存后即可生效，无需重载插件。',
    f('enable_offline_notifications', '开启 QQ 掉线通知', '默认关闭。开启前先确认 SMTP 能正常发信。', 'boolean') +
    f('offline_notify_on_recovery', '连接恢复后通知', '附上本次掉线时长。', 'boolean') +
    `<div class="form-grid">${f('offline_check_interval_seconds', '检测间隔（秒）', '通常建议 60–180 秒。', 'number', { min:30, max:3600 })}${f('offline_failure_threshold', '连续失败次数', '短暂波动不会立刻通知；达到次数后只通知一次。', 'number', { min:1, max:10 })}${f('offline_notify_recipients', '通知到哪些邮箱', '每行一个邮箱。留空时使用主人邮箱；多位收件人逐一投递，互不暴露地址。', 'list')}${f('offline_monitored_platforms', '监控哪些平台', '默认 aiocqhttp，覆盖 NapCat 与 LLOneBot。也可每行填写一个 AstrBot 平台 ID；留空不监控。', 'list')}</div>`);
  const templateCard = card('通知模板', '自带掉线与恢复样式，也可以写成自己的提醒。',
    notice('可用变量：{{nickname}} 昵称 · {{bot_id}} QQ 号 · {{platform}} 平台 · {{time}} 时间 · {{reason}} 原因 · {{downtime}} 掉线时长。变量会安全替换，模板只用于邮件。') +
    `<div class="form-grid">${f('offline_subject_template', '掉线邮件主题', '纯文本主题。', 'text')}${f('recovery_subject_template', '恢复邮件主题', '纯文本主题。', 'text')}${f('offline_body_template', '掉线邮件正文', '支持 HTML。留空保存会恢复默认样式。', 'template')}${f('recovery_body_template', '恢复邮件正文', '支持 HTML。留空保存会恢复默认样式。', 'template')}</div><div id="template-preview" class="template-preview" hidden></div>`,
    button('template-preview', '预览模板', 'secondary small', 'eye'));
  return heading('掉线守护', 'QQ 掉线时发封邮件，重新上线后报个平安。', button('offline-test', '发送测试通知', 'secondary', 'send'))
    + statusGrid(settings, off)
    + `<section id="offline-overview" class="card offline-overview" aria-label="掉线历史概览"></section>`
    + `<form id="monitor-form" class="settings-form">${guardCard}${templateCard}${saveBar()}</form>`;
}

export async function monitorView(ctx) {
  const s = ctx.store.state.monitor || (ctx.store.state.monitor = { historyPeriod:'24h', historyAccount:'' });
  const payload = await ctx.api.get('webui/settings');
  const off = await ctx.api.get('webui/offline-status');
  if (!ctx.isCurrent()) return;

  let snapshot = payload.settings;
  const root = ctx.container;
  root.innerHTML = markup(snapshot, off);
  $('#offline-overview').innerHTML = historyShell(s.historyPeriod, s.historyAccount);

  let historyEpoch = 0;
  let timer = null;
  let disposed = false;

  async function loadHistory(background = false) {
    const content = $('#history-content');
    if (!content || !ctx.isCurrent()) return;
    const myEpoch = ++historyEpoch;
    if (!background) content.setAttribute('aria-busy', 'true');
    try {
      const data = await ctx.api.get('webui/offline-history', { period:s.historyPeriod, account:s.historyAccount });
      if (myEpoch !== historyEpoch || !ctx.isCurrent()) return;
      if (!data.available) throw new Error(data.message || '掉线历史暂时不可用。');
      const overview = $('#offline-overview');
      if (background && (overview.contains(document.activeElement) || overview.matches(':hover') || content.querySelector('details[open]'))) return;
      const accounts = data.accounts || [], select = $('#history-account');
      select.innerHTML = '<option value="">全部账号</option>' + accounts.map(a => '<option value="' + esc(a.key) + '">' + esc((a.nickname || a.bot_id || a.key) + (a.nickname && a.bot_id ? ' · ' + a.bot_id : '')) + '</option>').join('');
      if (s.historyAccount && !accounts.some(a => a.key === s.historyAccount)) select.insertAdjacentHTML('beforeend', '<option value="' + esc(s.historyAccount) + '">' + esc(s.historyAccount) + '（暂无记录）</option>');
      select.value = s.historyAccount;
      $('#history-alert').innerHTML = '';
      content.innerHTML = historyContent(data);
      bindHistory(content);
    } catch (error) {
      if (myEpoch === historyEpoch && ctx.isCurrent()) {
        if (background) $('#history-alert').innerHTML = notice('历史刷新失败，当前显示上次成功的数据。' + error.message, 'warning');
        else { $('#history-alert').innerHTML = ''; content.innerHTML = historyError(error.message); }
      }
    } finally {
      if (myEpoch === historyEpoch && ctx.isCurrent()) content.setAttribute('aria-busy', 'false');
    }
  }

  function schedule() {
    timer = setTimeout(async () => {
      if (disposed || !ctx.isCurrent() || document.hidden) { if (!disposed && ctx.isCurrent()) schedule(); return; }
      try {
        const fresh = await ctx.api.get('webui/offline-status');
        if (disposed || !ctx.isCurrent()) return;
        const probe = document.createElement('div');
        probe.innerHTML = statusGrid(snapshot, fresh);
        root.querySelector('.monitor-status')?.replaceWith(probe.firstElementChild);
      } catch { /* 显式刷新会在不打断编辑的情况下暴露错误 */ }
      if (disposed || !ctx.isCurrent()) return;
      const history = $('#offline-overview');
      if (history && !history.contains(document.activeElement) && !history.matches(':hover') && !history.querySelector('details[open]')) void loadHistory(true);
      schedule();
    }, REFRESH_MS);
  }

  const markDirty = e => { if (e.target.closest('#monitor-form')) ctx.setDirty(true); };

  const cleanups = [
    bindActions(root, {
      'history-refresh': () => loadHistory(),
      'offline-test': async () => {
        if (ctx.getDirty()) { toast('请先保存修改，再发送测试通知。'); return; }
        const ok = await confirmDialog('发送掉线测试通知？', ctx.api.demo ? '当前是演示预览，只展示测试结果，不会发送真实邮件。' : '会向已配置的通知邮箱发送一封真实测试邮件。未配置通知邮箱时，使用主人邮箱。');
        if (ok) toast((await ctx.api.post('webui/offline-test')).message);
      },
      'template-preview': () => {
        const values = formValues($('#monitor-form')), box = $('#template-preview');
        box.hidden = false;
        box.innerHTML = '<div class="preview-pair"><h3>掉线通知</h3>' + safePreview(values.offline_body_template, '掉线通知模板') + '</div><div class="preview-pair"><h3>恢复通知</h3>' + safePreview(values.recovery_body_template, '恢复通知模板') + '</div>';
      },
    }, error => toast(error.message || '操作失败')),
    delegate(root, 'submit', '#monitor-form', async (e) => {
      e.preventDefault();
      const next = await saveSettings(ctx, e.target, snapshot);
      if (next) { snapshot = next; ctx.rerender(); }
    }),
    on(root, 'change', e => {
      if (e.target.id === 'history-period') { s.historyPeriod = e.target.value; void loadHistory(); }
      else if (e.target.id === 'history-account') { s.historyAccount = e.target.value; void loadHistory(); }
      else if (e.target.closest('#monitor-form')) markDirty(e);
    }),
    on(root, 'input', markDirty),
  ];

  await loadHistory();
  schedule();

  return group(() => { disposed = true; clearTimeout(timer); }, ...cleanups);
}

