// 邮件中心：分类、搜索筛选、分页、信件阅读器（文本/安全预览/专注/已读/收藏/归档）。
import { esc, icon, statusBadge, date, heading, empty, notice, button, safeFrame, loading } from './ui.js';
import { $, on, delegate, group } from './dom.js';
import { toast, confirmDialog } from './shell.js';

const PAGE = 20;
const recipientText = item => (item.recipients || []).map(v => v.address).join('、') || '无收件人记录';

function toolbar(s) {
  const folders = [['sent', '发件箱', 'send'], ['inbox', '投递副本', 'inbox'], ['errors', '投递异常', 'alert']];
  return `<div class="mail-toolbar">
    <div class="tabs" role="group" aria-label="邮件分类">${folders.map(([f, l, ic]) => `<button type="button" data-folder="${f}" class="${s.folder === f ? 'active' : ''}" aria-pressed="${s.folder === f}">${icon(ic)}<span>${l}</span></button>`).join('')}</div>
    <form id="search-form" class="search-form"><span class="search-ico" aria-hidden="true">${icon('search')}</span><input name="query" aria-label="搜索邮件主题" type="search" placeholder="搜索已保存的邮件主题…" value="${esc(s.query)}"></form>
    <div class="mail-filters">
      <select id="status-filter" aria-label="筛选投递状态"><option value="">全部状态</option>${[['submitted', 'SMTP 已接受'], ['partial', '部分接受'], ['failed', '发送失败'], ['unknown', '状态未知']].map(([k, l]) => `<option value="${k}" ${s.status === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select id="format-filter" aria-label="筛选邮件格式"><option value="">全部格式</option><option value="html" ${s.format === 'html' ? 'selected' : ''}>HTML 邮件</option><option value="plain" ${s.format === 'plain' ? 'selected' : ''}>文本邮件</option></select>
    </div>
  </div>`;
}

function listMarkup(result, s) {
  if (!result.enabled) return empty('邮件记录尚未开启', '请先在投递设置中开启本地邮件历史。');
  const list = result.items || [];
  const rows = list.length ? list.map((item, index) => {
    const active = s.selectedIndex === index;
    return `<button type="button" class="message-button ${active ? 'active' : ''}" data-item="${index}" aria-pressed="${active}">
      <span class="message-subject">${esc(item.subject || '未保存邮件内容')}</span>
      <span class="message-meta"><span class="message-to">${esc(recipientText(item))}</span><time>${esc(date(item.created_at, true))}</time></span>
      <span class="message-bottom">${statusBadge(item.status)}<span class="chip">${item.content_format === 'html' ? 'HTML' : '文本'}</span>${item.attachment_count ? `<span class="chip">附件 ${item.attachment_count}</span>` : ''}${item.recipient?.is_starred ? '<span class="star-mark" aria-label="已收藏">★</span>' : ''}</span>
    </button>`;
  }).join('') : empty('这里暂时没有邮件', '试试其他分类或清空搜索条件。');
  return `<div class="mail-list-head"><span>${result.total || 0} 封记录</span><span class="muted">地址已脱敏</span></div>${rows}
    <div class="pagination"><button class="button secondary small" type="button" data-page="prev" ${!s.offset ? 'disabled' : ''}>上一页</button><span>第 ${Math.floor(s.offset / PAGE) + 1} 页</span><button class="button secondary small" type="button" data-page="next" ${!result.has_more ? 'disabled' : ''}>下一页</button></div>`;
}

function readerMarkup(detail, item, tab, focused) {
  const recipient = item?.recipient;
  const body = !detail.content_saved
    ? empty('这封邮件只保存了投递状态', '开启“保存主题与正文”后，新发送邮件的内容才会被记录。')
    : tab === 'html' ? safeFrame(detail.html_preview, '邮件安全预览') : `<div class="reader-body">${esc(detail.plain_body || '没有纯文本内容')}</div>`;
  return `<div class="reader-toolbar">
      <div class="tabs" role="group" aria-label="阅读格式"><button type="button" data-reader-tab="plain" class="${tab === 'plain' ? 'active' : ''}" aria-pressed="${tab === 'plain'}">文本</button><button type="button" data-reader-tab="html" class="${tab === 'html' ? 'active' : ''}" aria-pressed="${tab === 'html'}" ${!detail.html_preview ? 'disabled' : ''}>安全预览</button></div>
      ${button('focus-reader', focused ? '退出专注' : '专注阅读', 'secondary small', 'expand')}
    </div>
    <article class="letter">
      <header class="letter-head"><h2>${esc(detail.subject || '未保存邮件内容')}</h2><div class="reader-meta"><span>${esc(date(detail.created_at))}</span><span>${esc(recipientText(detail))}</span>${statusBadge(detail.status)}</div></header>
      ${body}
      ${detail.attachment_names?.length ? `<div class="attachment-list">${detail.attachment_names.map(n => `<span class="chip">${icon('layers')}${esc(n)}</span>`).join('')}</div><p class="monitor-note">仅展示附件名称，附件文件不会保存在本地邮件历史中。</p>` : ''}
      ${detail.error_code ? notice('投递异常：' + detail.error_code, 'error') : ''}
    </article>
    <div class="reader-actions">${recipient?.status === 'accepted' ? button('mark-read', recipient.is_read ? '标为未读' : '标为已读', 'secondary small') + button('star', recipient.is_starred ? '取消收藏' : '收藏', 'secondary small', 'star') + button('archive', '归档本地副本', 'secondary small') : ''}</div>
    <p class="monitor-note">SMTP 接受表示服务器收到投递请求，不代表收件人已经收到或阅读。</p>`;
}

export async function mailboxView(ctx) {
  const s = ctx.store.state.mailbox || (ctx.store.state.mailbox = { folder: 'sent', query: '', status: '', format: '', offset: 0 });
  s.items = []; s.selectedIndex = null; s.detail = null; s.tab = 'plain'; s.focused = false;

  ctx.container.innerHTML = heading('邮件中心', '回看已发邮件和本地投递副本，了解每一次投递的结果。')
    + notice('这里只展示本插件发送的记录与本地副本，不会读取邮箱服务商的收件箱。')
    + toolbar(s)
    + `<div class="mail-layout"><section class="mail-list" aria-label="邮件列表" id="mail-list">${loading()}</section><section class="reader" id="reader" aria-label="邮件阅读区">${empty('选一封邮件，慢慢读', '点击左侧列表，查看邮件内容和投递结果。', 'mail')}</section></div>`;

  let listEpoch = 0, detailEpoch = 0;

  function renderReader() {
    const el = $('#reader'); if (!el) return;
    if (s.detail) { el.innerHTML = readerMarkup(s.detail, s.items[s.selectedIndex], s.tab, s.focused); el.classList.toggle('focus-mode', s.focused); }
  }

  async function loadMessages() {
    const myEpoch = ++listEpoch; const el = $('#mail-list'); if (!el) return;
    ++detailEpoch; s.selectedIndex = null; s.detail = null; s.focused = false;
    const reader = $('#reader'); reader.classList.remove('focus-mode'); reader.innerHTML = empty('选一封邮件，慢慢读', '点击列表，查看内容和投递结果。', 'mail');
    el.innerHTML = loading();
    try {
      const result = await ctx.api.get('webui/messages', { folder: s.folder, query: s.query, status: s.status, format: s.format, limit: PAGE, offset: s.offset });
      if (myEpoch !== listEpoch || !ctx.isCurrent()) return;
      s.items = result.items || [];
      el.innerHTML = listMarkup(result, s);
    } catch (error) {
      if (myEpoch === listEpoch) el.innerHTML = `<div class="empty"><h2>暂时无法加载</h2><p>${esc(error.message || '请求失败')}</p></div>`;
    }
  }

  async function selectItem(index) {
    const item = s.items[index]; if (!item) return;
    const myEpoch = ++detailEpoch; s.selectedIndex = index; s.detail = null;
    $('#mail-list').querySelectorAll('[data-item]').forEach(el => { const active = Number(el.dataset.item) === index; el.classList.toggle('active', active); el.setAttribute('aria-pressed', String(active)); });
    $('#reader').innerHTML = loading();
    try {
      const detail = await ctx.api.get('webui/message/' + item.id);
      if (myEpoch !== detailEpoch || !ctx.isCurrent()) return;
      s.detail = detail; s.tab = detail.html_preview ? 'html' : 'plain'; renderReader();
      if (matchMedia('(max-width:860px)').matches) $('#reader').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) { if (myEpoch === detailEpoch) $('#reader').innerHTML = empty('暂时无法阅读', error.message, 'alert'); }
  }

  async function mailboxState(action) {
    const item = s.items[s.selectedIndex], recipient = item?.recipient; if (!recipient) return;
    const values = action === 'mark-read' ? { is_read: !recipient.is_read } : action === 'star' ? { is_starred: !recipient.is_starred } : { archived: true };
    await ctx.api.post('webui/mailbox-state', { message_id: item.id, recipient_token: recipient.token, ...values });
    Object.assign(recipient, values); renderReader();
    if (action === 'archive') await loadMessages();
  }

  const root = ctx.container;
  const cleanups = [
    delegate(root, 'click', '[data-folder]', (_e, el) => { s.folder = el.dataset.folder; s.offset = 0; root.querySelectorAll('[data-folder]').forEach(b => { const active = b === el; b.classList.toggle('active', active); b.setAttribute('aria-pressed', String(active)); }); loadMessages(); }),
    delegate(root, 'click', '[data-page]', (_e, el) => { if (el.disabled) return; s.offset = Math.max(0, s.offset + (el.dataset.page === 'next' ? PAGE : -PAGE)); loadMessages(); }),
    delegate(root, 'click', '[data-item]', (_e, el) => selectItem(Number(el.dataset.item))),
    delegate(root, 'click', '[data-reader-tab]', (_e, el) => { if (el.disabled) return; s.tab = el.dataset.readerTab; renderReader(); }),
    delegate(root, 'click', '[data-action]', async (_e, el) => {
      const action = el.dataset.action; if (el.disabled) return;
      el.disabled = true;
      try {
        if (action === 'focus-reader') { s.focused = !s.focused; renderReader(); }
        else if (['mark-read', 'star', 'archive'].includes(action)) await mailboxState(action);
      } catch (error) { toast(error.message || '操作失败'); }
      finally { el.disabled = false; }
    }),
    on(root, 'submit', e => { if (e.target.id === 'search-form') { e.preventDefault(); s.query = new FormData(e.target).get('query').trim(); s.offset = 0; loadMessages(); } }),
    on(root, 'change', e => {
      if (e.target.id === 'status-filter') { s.status = e.target.value; s.offset = 0; loadMessages(); }
      if (e.target.id === 'format-filter') { s.format = e.target.value; s.offset = 0; loadMessages(); }
    }),
    on(document, 'keydown', e => { if (e.key === 'Escape' && s.focused) { s.focused = false; renderReader(); } }),
  ];

  await loadMessages();
  return group(...cleanups);
}
