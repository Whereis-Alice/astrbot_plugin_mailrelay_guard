// 投递设置：投递与权限 / HTML 邮件 / 发送频率 / 附件 / 本地记录。保存只提交变化项。
import { heading, card, notice, button, field } from './ui.js';
import { on, delegate, bindActions, group } from './dom.js';
import { toast, confirmDialog } from './shell.js';
import { saveBar, saveSettings } from './forms.js';

function markup(settings) {
  const f = (name, label, help, type, opts) => field(settings, name, label, help, type, opts);
  const switches = defs => defs.map(([name, label, help]) => f(name, label, help, 'boolean')).join('');
  const nums = defs => `<div class="form-grid">${defs.map(([name, label, min, max]) => f(name, label, '', 'number', { min, max })).join('')}</div>`;

  const deliver = card('投递与权限', '每次发信都会在服务端检查收件人权限。', switches([
    ['enabled', '启用邮件服务', '关闭时停止发送邮件与掉线通知。'],
    ['enable_owner_delivery', '允许通知主人', '仅配置的主人身份可以触发。'],
    ['enable_self_delivery', '允许用户发给自己', '只能发到当前用户解析或绑定的邮箱。'],
    ['require_private_chat_for_self_delivery', '自助投递仅限私聊', '避免在群聊中暴露邮件操作。'],
    ['enable_admin_other_delivery', '允许管理员发给别人', '仍需要 AstrBot 管理员身份和插件身份白名单。'],
  ]));
  const html = card('HTML 邮件', '开启或关闭 HTML 邮件工具后，需要重载插件。',
    switches([
      ['enable_html_mail', '开启 HTML 模板邮件', '让助手使用 HTML 格式发送邮件。'],
      ['sanitize_html_before_send', '发送前严格清洗', '移除危险标签、脚本和不允许的样式。'],
      ['html_allow_links', '允许邮件链接', '仅保留检查通过的安全链接。'],
      ['html_allow_remote_images', '允许远程图片', '建议同时设置下方域名白名单。'],
    ]) + `<div class="form-grid">${f('html_remote_image_allowed_domains', '允许的图片域名', '每行一个域名。内嵌 CID 图片不受此项影响。', 'list')}${f('max_html_body_chars', 'HTML 正文长度上限', '单位：字符。', 'number', { min:1, max:200000 })}</div>`);
  const rate = card('发送频率', '限制批量投递和反复重试。', nums([
    ['max_messages_per_hour', '全局每小时成功投递上限', 1, 1000],
    ['max_successful_messages_per_actor_per_hour', '每位用户每小时成功上限', 1, 100],
    ['max_delivery_attempts_per_actor_per_hour', '每位用户每小时尝试上限', 1, 200],
    ['actor_min_send_interval_seconds', '用户发送冷却（秒）', 0, 3600],
  ]));
  const attach = card('图片与文件附件', '仅允许消息媒体和当前会话工作区中的文件。',
    switches([
      ['enable_attachments', '允许邮件附件', '图片和文件也会检查数量与大小。'],
      ['allow_message_images', '允许消息图片', '包括当前消息与引用回复中的图片。'],
      ['allow_message_files', '允许消息文件', '包括当前消息与引用回复中的文件。'],
      ['allow_workspace_attachments', '允许会话生成的文件', '只读取当前会话允许的工作区路径。'],
      ['enable_inline_images', '允许 CID 内嵌图片', 'HTML 模板可用 {{image_1}} 等占位符。'],
    ]) + nums([
      ['max_attachments_per_message', '最多附件数量', 1, 20],
      ['max_attachment_size_mb', '单个附件上限（MB）', 1, 50],
      ['max_total_attachment_size_mb', '附件总大小上限（MB）', 1, 100],
      ['attachment_fetch_timeout_seconds', '附件读取超时（秒）', 5, 120],
    ]) + `<div class="field-block">${f('blocked_attachment_extensions', '禁止的附件扩展名', '每行一个，如 .exe。', 'list')}</div>`);
  const historyCard = card('本地邮件记录', '收件人地址始终脱敏，默认不保存邮件内容。',
    switches([
      ['mail_history_enabled', '保存本地投递记录', '记录投递结果与附件名称，不保存附件文件。'],
      ['mail_history_store_content', '保存主题与正文', '开启后仅影响新邮件。邮件内容会保存在 AstrBot 本地，请按需要选择。'],
    ]) + nums([
      ['mail_history_retention_days', '保留天数', 1, 3650],
      ['mail_history_max_records', '最多记录数', 20, 5000],
    ]) + `<div class="button-row field-block">${button('clear-history', '清空本地记录', 'secondary small', 'trash')}</div>`);

  return heading('投递设置', '管理投递能力、内容保护、附件和本地记录。')
    + notice('邮箱账号、SMTP 授权码、主人邮箱和身份白名单，请在 AstrBot → 插件 → 我的邮件 → 配置中填写。')
    + `<form id="settings-form" class="settings-form"><div class="grid-two"><div class="stack">${deliver}${html}${rate}</div><div class="stack">${attach}${historyCard}</div></div>${saveBar()}</form>`;
}

export async function settingsView(ctx) {
  const payload = await ctx.api.get('webui/settings');
  if (!ctx.isCurrent()) return;
  let snapshot = payload.settings;
  const root = ctx.container;
  root.innerHTML = markup(snapshot);

  const markDirty = e => { if (e.target.closest('#settings-form')) ctx.setDirty(true); };

  const cleanups = [
    bindActions(root, {
      'clear-history': async () => {
        const ok = await confirmDialog('清空本地邮件记录？', '这会永久删除本插件的本地历史和投递副本，邮箱服务商中的邮件不受影响。');
        if (ok) { const result = await ctx.api.post('webui/history-clear', { confirm:'clear-mail-history' }); toast(`已清空 ${result.removed} 条本地记录。`); }
      },
    }, error => toast(error.message || '操作失败')),
    delegate(root, 'submit', '#settings-form', async (e) => {
      e.preventDefault();
      const next = await saveSettings(ctx, e.target, snapshot);
      if (next) { snapshot = next; ctx.rerender(); }
    }),
    on(root, 'input', markDirty),
    on(root, 'change', markDirty),
  ];

  return group(...cleanups);
}
