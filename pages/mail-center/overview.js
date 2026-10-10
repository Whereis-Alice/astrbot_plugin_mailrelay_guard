// 运行概览：hero + 统计卡 + 最近邮件 + 连接 + 能力。只读页面。
import { esc, icon, badge, statusBadge, date, heading, empty, notice, button, row, card, logoMark } from './ui.js';
import { bindActions } from './dom.js';
import { toast } from './shell.js';

const recipientText = item => (item.recipients || []).map(v => v.address).join('、') || '无收件人记录';
const recent = item => `<div class="recent-row"><span class="recent-stamp" aria-hidden="true">${icon('mail')}</span><div class="recent-main"><strong>${esc(item.subject || '未保存邮件内容')}</strong><small>${esc(recipientText(item))} · ${esc(date(item.created_at, true))}</small></div>${statusBadge(item.status)}</div>`;

function markup(summary, recentItems) {
  const history = summary.history || {}, off = summary.offline || {}, features = summary.features || {};
  const monitored = off.monitored || [];
  const online = monitored.filter(i => i.online && i.probe_state !== 'unknown').length;
  const ready = summary.readiness === 'ready';
  const stats = [
    ['今日投递', history.today_total || 0, '已被 SMTP 接受 ' + (history.today_accepted || 0) + ' 封', 'mail'],
    ['累计记录', history.total || 0, '本地投递记录，不是远程收件箱', 'layers'],
    ['投递异常', (history.failed || 0) + (history.partial || 0), '失败或部分收件人被拒绝', 'alert'],
    ['连接守护', off.enabled ? online + '/' + monitored.length : '未启用', off.enabled ? '当前在线 / 受监控账号' : '开启后通过邮件接收掉线提醒', 'shield'],
  ];
  const statHtml = stats.map(([t, v, d, s]) => `<article class="stat-card"><div class="stat-top"><span>${esc(t)}</span><span class="stat-ico">${icon(s)}</span></div><div class="stat-value">${esc(v)}</div><div class="stat-foot">${esc(d)}</div></article>`).join('');

  const hero = `<section class="hero"><div class="hero-text"><div class="eyebrow">A LITTLE PEACE OF MIND</div><h2>${ready ? '每一封重要的邮件，都值得安心送达。' : '先连接邮箱，再开始安心投递。'}</h2><p>${ready ? '邮件服务已就绪。你可以查看近期记录，也可以为 QQ 机器人开启掉线守护。' : '请在 AstrBot 插件配置中填写邮箱账号、SMTP 授权码和发件地址。'}</p><a class="button hero-cta" href="#${ready ? 'mailbox' : 'settings'}">${ready ? '打开邮件中心' : '查看投递设置'} ${icon('arrow')}</a></div><div class="hero-stamp">${logoMark('hero')}</div></section>`;

  const svc = card('邮件服务', '邮箱凭据请在 AstrBot 插件配置中管理。',
    row('服务器', (summary.smtp?.host || '未填写') + ':' + (summary.smtp?.port || '—')) +
    row('连接加密', summary.smtp?.security?.toUpperCase() || '—') +
    row('服务状态', ready ? '配置就绪' : '需要配置'));

  const recentCard = card('最近的邮件', '重要的记录，随时回看。',
    recentItems.length ? recentItems.slice(0, 5).map(recent).join('') : empty('还没有邮件记录', '发送邮件后，记录会出现在这里。'),
    '<a class="button ghost small" href="#mailbox">查看全部 →</a>');

  const connCard = card('QQ 连接守护', '掉线时通知你，恢复时也报个平安。',
    monitored.length
      ? monitored.map(i => `<div class="status-line"><span class="status-ico">${icon('pulse')}</span><span class="grow">${esc(i.nickname || i.bot_id || i.key)}</span>${badge(i.probe_state === 'unknown' ? '待确认' : i.online ? '在线' : '离线', i.probe_state === 'unknown' ? 'warn' : i.online ? 'ok' : 'bad')}</div>`).join('')
      : empty(off.enabled ? '等待首次检测' : '让掉线变得可知', off.enabled ? '尚未发现匹配的 QQ 平台，请检查监控平台设置。' : '在掉线守护中设置通知邮箱并开启监控。', 'pulse'),
    '<a class="button ghost small" href="#monitor">管理守护 →</a>');

  const capCard = card('投递能力', '由服务端执行权限与内容检查。',
    [['HTML 模板', features.html_mail_enabled], ['图片与文件附件', features.attachments_enabled], ['本地记录', features.history_enabled], ['保存邮件正文', features.history_store_content]]
      .map(([n, onFlag]) => `<div class="status-line"><span class="status-ico">${icon(onFlag ? 'check' : 'sliders')}</span><span class="grow">${esc(n)}</span>${badge(onFlag ? '已开启' : '已关闭', onFlag ? 'ok' : 'neutral')}</div>`).join(''));

  return heading('运行概览', '把邮件、投递状态和 QQ 连接，放在一个安静的工作空间里。', button('smtp-test', '测试连接', 'secondary', 'refresh'))
    + hero
    + (!ready ? notice((summary.configuration_problems || []).join(' '), 'warning') : '')
    + `<div class="stat-grid">${statHtml}</div>`
    + `<div class="grid-two"><div class="stack">${recentCard}${svc}</div><div class="stack">${connCard}${capCard}</div></div>`;
}

export async function overviewView(ctx) {
  const [summary, messages] = await Promise.all([
    ctx.api.get('webui/summary'),
    ctx.api.get('webui/messages', { folder: 'sent', limit: 5 }),
  ]);
  if (!ctx.isCurrent()) return;
  ctx.container.innerHTML = markup(summary, messages.items || []);
  const stop = bindActions(ctx.container, {
    'smtp-test': async () => toast((await ctx.api.post('webui/smtp-probe')).message),
  }, e => toast(e.message || '操作失败'));
  return stop;
}
