const pluginBridge = window.AstrBotPluginPage;
const PAGE_SIZE = 30;
const THEME_KEY = "mailrelay-guard-theme";

const state = {
  activeView: "overview",
  folder: "sent",
  offset: 0,
  summary: null,
  settings: null,
  messages: null,
  selectedItem: null,
  selectedDetail: null,
  detailTab: "plain",
  demo: !pluginBridge,
};

const titles = {
  overview: ["运行概览", "查看 SMTP 就绪状态、近期投递和本地邮件归档。"],
  mailbox: ["邮件中心", "浏览发件箱、本地投递副本和投递异常；内容只在已启用本地归档时可见。"],
  settings: ["安全配置", "调整非敏感投递策略、HTML 清洗、归档与限流；SMTP 凭据仍由 AstrBot 原生配置管理。"],
};

const statusNames = {
  sending: "发送中",
  submitted: "SMTP 已接受",
  partial: "部分接受",
  failed: "失败",
  unknown: "状态未知",
  accepted: "已接受",
  refused: "被拒绝",
  pending: "等待中",
};

const modeNames = {
  owner: "主人通知",
  self: "发给自己",
  other: "管理员代发",
};

const mockSettings = {
  settings: {
    enabled: true,
    enable_owner_delivery: true,
    enable_self_delivery: true,
    enable_admin_other_delivery: true,
    require_private_chat_for_self_delivery: true,
    enable_html_mail: true,
    sanitize_html_before_send: true,
    html_allow_links: false,
    html_allow_remote_images: false,
    html_remote_image_allowed_domains: [],
    max_html_body_chars: 30000,
    mail_history_enabled: true,
    mail_history_store_content: true,
    mail_history_retention_days: 30,
    mail_history_max_records: 500,
    max_messages_per_hour: 30,
    max_successful_messages_per_actor_per_hour: 5,
    max_delivery_attempts_per_actor_per_hour: 8,
    actor_min_send_interval_seconds: 60,
  },
  restart_required_fields: ["enable_html_mail"],
  secret_fields: ["smtp_username", "smtp_password", "sender_address"],
};

const mockSummary = {
  version: "v1.3.0",
  readiness: "ready",
  configuration_problems: [],
  smtp: { host: "smtp.163.com", port: 465, security: "ssl", account_configured: true, sender_configured: true },
  features: {
    llm_tools_registered: true,
    html_tools_registered: true,
    html_mail_enabled: true,
    html_strict_cleaning: true,
    history_enabled: true,
    history_store_content: true,
    history_available: true,
  },
  history: { total: 18, submitted: 13, partial: 2, failed: 3, html: 6, latest_at: "2026-08-09T05:18:41+00:00", today_total: 4, today_accepted: 3 },
};

const mockItems = [
  {
    id: "7ba2d8f8ef0a4fae8f7949ed9f2071aa",
    created_at: "2026-08-09T05:18:41+00:00",
    completed_at: "2026-08-09T05:18:43+00:00",
    action: "llm_self",
    mode: "self",
    content_format: "html",
    status: "submitted",
    recipient_count: 1,
    accepted_count: 1,
    refused_count: 0,
    error_code: null,
    content_saved: true,
    subject: "Alice 的工作摘要",
    recipients: [{ token: "c1c6a1c2c3c4d5e6f7a8b9c0", address: "a***e@example.com", domain: "example.com", status: "accepted", is_read: false, is_starred: false, archived: false }],
  },
  {
    id: "6ac19b2d4a2c4a5d9f7ea98c1b2f3a4c",
    created_at: "2026-08-09T04:39:07+00:00",
    completed_at: "2026-08-09T04:39:08+00:00",
    action: "llm_other",
    mode: "other",
    content_format: "plain",
    status: "partial",
    recipient_count: 2,
    accepted_count: 1,
    refused_count: 1,
    error_code: null,
    content_saved: true,
    subject: "项目进度通知",
    recipients: [
      { token: "a9b8c7d6e5f4a3b2c1d0e9f8", address: "m***n@example.com", domain: "example.com", status: "accepted", is_read: false, is_starred: false, archived: false },
      { token: "0f1e2d3c4b5a697887969594", address: "r***d@example.net", domain: "example.net", status: "refused", is_read: false, is_starred: false, archived: false },
    ],
  },
  {
    id: "8d3fa1b8c2e44a90b5d72f1c3a9e6b4d",
    created_at: "2026-08-09T02:10:16+00:00",
    completed_at: "2026-08-09T02:10:37+00:00",
    action: "llm_owner",
    mode: "owner",
    content_format: "html",
    status: "failed",
    recipient_count: 1,
    accepted_count: 0,
    refused_count: 0,
    error_code: "transport_error",
    content_saved: false,
    subject: null,
    recipients: [{ token: "5a4b3c2d1e0f112233445566", address: "o***r@example.com", domain: "example.com", status: "failed", is_read: false, is_starred: false, archived: false }],
  },
];

const byId = (id) => document.getElementById(id);
const query = (selector, parent = document) => parent.querySelector(selector);
const all = (selector, parent = document) => [...parent.querySelectorAll(selector)];

function create(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function clear(element) {
  element.replaceChildren();
  return element;
}

function isApiError(value) {
  return Boolean(value && typeof value === "object" && value.status === "error");
}

function apiErrorMessage(value, fallback) {
  if (isApiError(value)) return value.message || fallback;
  return fallback;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

async function demoGet(endpoint, params = {}) {
  if (endpoint === "webui/summary") return clone(mockSummary);
  if (endpoint === "webui/settings") return clone(mockSettings);
  if (endpoint === "webui/messages") {
    const folder = params.folder || "sent";
    let items = [...mockItems];
    if (folder === "errors") items = items.filter((item) => ["partial", "failed", "unknown"].includes(item.status));
    if (folder === "inbox") {
      items = items
        .flatMap((item) => item.recipients.filter((recipient) => recipient.status === "accepted" && !recipient.archived).map((recipient) => ({ ...item, recipient, recipients: [recipient], is_read: recipient.is_read, is_starred: recipient.is_starred })))
        .filter((item) => !item.recipient.archived);
    }
    if (params.status) items = items.filter((item) => item.status === params.status);
    if (params.format) items = items.filter((item) => item.content_format === params.format);
    const needle = String(params.query || "").trim().toLowerCase();
    if (needle) {
      items = items.filter((item) => [item.subject, item.action, item.mode, ...item.recipients.map((recipient) => `${recipient.address} ${recipient.domain}`)].join(" ").toLowerCase().includes(needle));
    }
    const offset = Number(params.offset || 0);
    const limit = Number(params.limit || PAGE_SIZE);
    return { enabled: true, folder, content_recording: true, total: items.length, offset, limit, has_more: offset + limit < items.length, items: items.slice(offset, offset + limit) };
  }
  if (endpoint.startsWith("webui/message/")) {
    const id = endpoint.slice("webui/message/".length);
    const item = mockItems.find((candidate) => candidate.id === id);
    if (!item) return { status: "error", message: "未找到该邮件记录。" };
    return {
      ...clone(item),
      message_id: `<${item.id.slice(0, 12)}@mailrelay.local>`,
      actor_token: "22c04b3f969780cc8dfd22fa",
      plain_body: item.content_saved ? "Alice 已完成本次任务。此投递副本用于在邮件中心快速回看发送内容。" : null,
      html_preview: item.content_format === "html" && item.content_saved
        ? '<div style="max-width:620px;margin:20px auto;border:1px solid #c6d2e9;border-radius:8px;background-color:#f8fbff;color:#1d2942;padding:26px;font-family:Arial,sans-serif"><h1 style="color:#00889a;font-size:24px;margin:0 0 14px">任务已完成</h1><p style="font-size:15px;line-height:1.7;margin:0">Alice 已整理好你请求的内容，并生成这封安全预览邮件。</p><div style="border-top:1px solid #d9e3f2;margin-top:20px;padding-top:14px;color:#62708d;font-size:12px">由 MailRelay Guard 清洗后的 HTML 副本</div></div>'
        : "",
    };
  }
  return { status: "error", message: "演示数据中没有该接口。" };
}

async function demoPost(endpoint, body = {}) {
  if (endpoint === "webui/settings") {
    Object.assign(mockSettings.settings, body.settings || {});
    return { changed: Object.keys(body.settings || {}), restart_required: Object.prototype.hasOwnProperty.call(body.settings || {}, "enable_html_mail"), settings: clone(mockSettings) };
  }
  if (endpoint === "webui/smtp-probe") return { message: "SMTP 连接与登录测试成功，未发送邮件。" };
  if (endpoint === "webui/history-clear") {
    const removed = mockItems.length;
    mockItems.splice(0, mockItems.length);
    return { removed };
  }
  if (endpoint === "webui/mailbox-state") {
    const item = mockItems.find((candidate) => candidate.id === body.message_id);
    const recipient = item?.recipients.find((candidate) => candidate.token === body.recipient_token);
    if (!recipient) return { status: "error", message: "未找到可更新的 SMTP 已接受副本。" };
    ["is_read", "is_starred", "archived"].forEach((key) => { if (typeof body[key] === "boolean") recipient[key] = body[key]; });
    return { is_read: recipient.is_read, is_starred: recipient.is_starred, archived: recipient.archived };
  }
  return { status: "error", message: "演示数据中没有该接口。" };
}

async function apiGet(endpoint, params) {
  const result = state.demo ? await demoGet(endpoint, params) : await pluginBridge.apiGet(endpoint, params);
  if (isApiError(result)) throw new Error(apiErrorMessage(result, "读取数据失败。"));
  return result;
}

async function apiPost(endpoint, body) {
  const result = state.demo ? await demoPost(endpoint, body) : await pluginBridge.apiPost(endpoint, body);
  if (isApiError(result)) throw new Error(apiErrorMessage(result, "操作失败。"));
  return result;
}

function formatTime(value, includeDate = true) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return new Intl.DateTimeFormat("zh-CN", { month: includeDate ? "2-digit" : undefined, day: includeDate ? "2-digit" : undefined, hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function statusClass(status) {
  return ["submitted", "partial", "failed", "sending", "unknown", "accepted", "refused"].includes(status) ? status : "unknown";
}

function statusBadge(status) {
  return create("span", `status-badge ${statusClass(status)}`, statusNames[status] || "未知状态");
}

function formatBadge(format) {
  return create("span", "format-badge", format === "html" ? "HTML" : "TEXT");
}

function setNotice(message, kind = "", timeout = 0) {
  const notice = byId("notice");
  notice.textContent = message;
  notice.className = `notice ${kind}`.trim();
  notice.hidden = !message;
  if (timeout) window.setTimeout(() => { if (notice.textContent === message) notice.hidden = true; }, timeout);
}

function setConnection(mode, text) {
  const live = byId("live-status");
  live.textContent = text;
  live.className = `live-status ${mode}`.trim();
  const sidebar = byId("sidebar-connection");
  sidebar.textContent = text;
  sidebar.className = mode === "is-error" ? "is-error" : mode === "is-warning" ? "is-warning" : "";
}

function settingsValue(id) {
  const element = byId(id);
  return element?.value ?? "";
}

function showView(view) {
  state.activeView = view;
  all(".view").forEach((element) => element.classList.toggle("active", element.id === `view-${view}`));
  all(".nav-button").forEach((element) => {
    const active = element.dataset.view === view;
    element.classList.toggle("active", active);
    element.toggleAttribute("aria-current", active);
  });
  const [title, subtitle] = titles[view];
  byId("page-title").textContent = title;
  byId("page-subtitle").textContent = subtitle;
  byId("sidebar").classList.remove("open");
  byId("mobile-nav-toggle").setAttribute("aria-expanded", "false");
  if (view === "mailbox" && !state.messages) void loadMailbox();
  if (view === "settings" && !state.settings) void loadSettings();
}

function setFolder(folder) {
  state.folder = folder;
  state.offset = 0;
  state.selectedItem = null;
  state.selectedDetail = null;
  all(".folder-tab").forEach((element) => {
    const active = element.dataset.folder === folder;
    element.classList.toggle("active", active);
    element.setAttribute("aria-selected", String(active));
  });
  byId("mailbox-disclaimer").textContent = folder === "inbox"
    ? "本地投递副本仅代表 SMTP 服务端已经接受该收件人。它不是 IMAP/POP3 真实收件箱，不代表最终送达或对方已阅读。"
    : folder === "errors"
      ? "这里显示失败、部分接受和状态未知的本地投递记录。SMTP 拒绝状态会按收件人保留。"
      : "发件箱保存本地投递尝试。默认只保留脱敏元数据；开启内容归档后才可查看主题和正文。";
  renderDetailEmpty();
  void loadMailbox();
}

function renderSummary(summary) {
  state.summary = summary;
  const history = summary.history || {};
  const features = summary.features || {};
  const readiness = summary.readiness === "ready";
  byId("metric-today-total").textContent = String(history.today_total ?? 0);
  byId("metric-today-hint").textContent = history.latest_at ? `最近投递 ${formatTime(history.latest_at)}` : "暂未记录投递";
  byId("metric-accepted").textContent = String(history.today_accepted ?? 0);
  byId("metric-errors").textContent = String((history.failed || 0) + (history.partial || 0));
  byId("metric-html").textContent = String(history.html ?? 0);
  byId("readiness-title").textContent = readiness ? "SMTP 配置已就绪" : "SMTP 仍需配置";
  const smtp = summary.smtp || {};
  const problems = summary.configuration_problems || [];
  byId("readiness-copy").textContent = readiness
    ? `${smtp.host || "SMTP"}:${smtp.port || "--"} 使用 ${String(smtp.security || "--").toUpperCase()}。账号与授权码不会显示在此页面。`
    : problems.length ? problems.join(" ") : "请在 AstrBot 原生插件配置中完成 SMTP 参数。";
  setConnection(readiness ? "is-ready" : "is-warning", readiness ? "服务就绪" : "需要配置");
  renderGuardSummary(features, smtp, history);
}

function renderGuardSummary(features, smtp, history) {
  const container = clear(byId("guard-summary"));
  const records = [
    ["SMTP 凭据", smtp.account_configured && smtp.sender_configured ? "已配置" : "未完成", smtp.account_configured && smtp.sender_configured ? "good" : "warn"],
    ["LLM 邮件工具", features.llm_tools_registered ? "已注册" : "未注册", features.llm_tools_registered ? "good" : "warn"],
    ["HTML 清洗", features.html_mail_enabled ? (features.html_strict_cleaning ? "严格清洗" : "基础清洗") : "未启用", features.html_strict_cleaning ? "good" : "warn"],
    ["本地历史", features.history_enabled ? (features.history_store_content ? "含内容归档" : "仅脱敏元数据") : "已关闭", features.history_enabled ? "good" : "warn"],
    ["累计记录", `${history.total ?? 0} 封`, ""],
  ];
  records.forEach(([label, value, className]) => {
    const row = create("div");
    row.append(create("dt", "", label), create("dd", className, value));
    container.append(row);
  });
}

function messageTitle(item) {
  return item.subject || (item.content_saved ? "（未填写主题）" : "内容未归档的投递记录");
}

function recipientText(item) {
  const recipients = item.recipients || [];
  if (!recipients.length) return "没有收件人信息";
  return recipients.map((recipient) => recipient.address || recipient.domain || "(unknown)").join(" · ");
}

function renderRecent(items) {
  const container = clear(byId("recent-list"));
  if (!items.length) {
    const empty = create("div", "empty-state", "尚无本地投递记录。");
    container.append(empty);
    return;
  }
  items.forEach((item) => {
    const row = create("article", "recent-item");
    const content = create("div");
    content.append(create("div", "recent-item-title", messageTitle(item)));
    const meta = create("div", "recent-item-meta");
    meta.append(create("span", "", modeNames[item.mode] || item.mode || "投递"));
    meta.append(create("span", "", recipientText(item)));
    meta.append(create("span", "", formatTime(item.created_at)));
    content.append(meta);
    const status = create("div");
    status.append(statusBadge(item.status));
    row.append(content, status);
    container.append(row);
  });
}

async function loadOverview() {
  const summary = await apiGet("webui/summary");
  renderSummary(summary);
  const recent = await apiGet("webui/messages", { folder: "sent", limit: 5, offset: 0 });
  renderRecent(recent.items || []);
}

function getMailboxParams(offset = state.offset) {
  return {
    folder: state.folder,
    query: settingsValue("message-search").trim(),
    status: settingsValue("message-status"),
    format: settingsValue("message-format"),
    limit: PAGE_SIZE,
    offset,
  };
}

function renderMessageList(payload) {
  const container = clear(byId("message-list"));
  const items = payload.items || [];
  byId("message-count").textContent = payload.enabled === false
    ? "本地历史未启用"
    : `共 ${payload.total ?? 0} 条`;
  byId("messages-page").textContent = `第 ${Math.floor((payload.offset || 0) / PAGE_SIZE) + 1} 页`;
  byId("messages-previous").disabled = !payload.offset;
  byId("messages-next").disabled = !payload.has_more;
  if (payload.enabled === false) {
    container.append(create("div", "empty-state", "本地邮件历史尚未启用。可在“安全配置”中开启投递历史。"));
    return;
  }
  if (!items.length) {
    container.append(create("div", "empty-state", "没有符合筛选条件的邮件记录。"));
    return;
  }
  items.forEach((item) => {
    const row = create("button", "message-row", "");
    row.type = "button";
    row.dataset.messageId = item.id;
    if (state.selectedItem?.id === item.id && (!item.recipient || state.selectedItem?.recipient?.token === item.recipient.token)) row.classList.add("active");
    if (state.folder === "inbox" && !item.is_read) row.classList.add("unread");
    const main = create("div");
    main.append(create("div", "message-row-title", messageTitle(item)), create("div", "message-row-recipient", recipientText(item)));
    const meta = create("div", "message-row-meta");
    meta.append(statusBadge(item.status), formatBadge(item.content_format));
    meta.append(create("span", "", formatTime(item.created_at, false)));
    row.append(main, meta);
    row.addEventListener("click", () => { void selectMessage(item); });
    container.append(row);
  });
}

async function loadMailbox(offset = state.offset) {
  state.offset = Math.max(0, Number(offset) || 0);
  const list = byId("message-list");
  clear(list).append(create("div", "empty-state", "正在读取本地邮件历史..."));
  try {
    const payload = await apiGet("webui/messages", getMailboxParams(state.offset));
    state.messages = payload;
    renderMessageList(payload);
  } catch (error) {
    clear(list).append(create("div", "empty-state", error.message || "读取邮件列表失败。"));
    setNotice(error.message || "读取邮件列表失败。", "error");
  }
}

function renderDetailEmpty() {
  const detail = clear(byId("message-detail"));
  query(".mailbox-grid")?.classList.remove("detail-open");
  detail.classList.remove("open");
  const empty = create("div", "detail-empty");
  empty.append(create("span", "", "MAIL"), create("h2", "", "选择一封邮件"), create("p", "", "详情只在打开记录时加载。主题、正文和 HTML 是否可见，取决于本地内容归档设置。"));
  detail.append(empty);
}

function metadataCell(label, value) {
  const cell = create("div");
  cell.append(create("span", "", label), create("strong", "", value));
  return cell;
}

function previewDocument(html) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; font-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'"><style>html{background:#fff;color:#172039}body{max-width:760px;margin:0 auto;padding:20px;line-height:1.6;overflow-wrap:anywhere}table{max-width:100%!important}img{display:none!important}</style></head><body>${html}</body></html>`;
}

function renderDetailContent(container, detail) {
  const hasContent = Boolean(detail.content_saved);
  if (!hasContent) {
    container.append(create("div", "content-placeholder", "此记录未保存主题和正文。默认归档只保留脱敏投递元数据；在“安全配置”中启用“保存主题和正文”后，后续 SMTP 已接受的邮件才会保存内容。"));
    return;
  }
  if (state.detailTab === "preview") {
    if (!detail.html_preview) {
      container.append(create("div", "content-placeholder", "这是一封纯文本邮件，或没有可预览的 HTML 内容。"));
      return;
    }
    container.append(create("p", "preview-note", "预览在禁脚本、禁网络、禁跳转的沙盒中二次清洗。远程图片和链接不会加载或可点击。"));
    const frame = create("iframe", "mail-preview-frame");
    frame.setAttribute("sandbox", "");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.title = "已清洗的 HTML 邮件预览";
    frame.srcdoc = previewDocument(detail.html_preview);
    container.append(frame);
    return;
  }
  if (state.detailTab === "source") {
    if (!detail.html_preview) {
      container.append(create("div", "content-placeholder", "这是一封纯文本邮件，没有可显示的 HTML 安全源码。"));
      return;
    }
    container.append(create("p", "preview-note", "这里展示的是给 Dashboard 预览使用的二次清洗后 HTML，不包含远程资源或链接。"));
    container.append(create("pre", "mail-source", detail.html_preview));
    return;
  }
  container.append(create("pre", "mail-plain", detail.plain_body || "邮件正文为空。"));
}

function renderMessageDetail(detail) {
  const root = clear(byId("message-detail"));
  root.classList.add("open");
  const back = create("button", "back-to-list", "返回列表");
  back.type = "button";
  back.addEventListener("click", () => {
    state.selectedItem = null;
    state.selectedDetail = null;
    renderDetailEmpty();
    renderMessageList(state.messages || { items: [] });
  });
  root.append(back);
  const top = create("header", "detail-top");
  const topRow = create("div", "detail-top-row");
  const titleWrap = create("div");
  titleWrap.append(create("p", "section-kicker", `${modeNames[detail.mode] || detail.mode || "投递"} / ${detail.content_format === "html" ? "HTML" : "TEXT"}`), create("h2", "detail-subject", detail.subject || "内容未归档的投递记录"), create("p", "detail-muted", `记录于 ${formatTime(detail.created_at)}${detail.completed_at ? `，完成于 ${formatTime(detail.completed_at)}` : ""}`));
  const badgeWrap = create("div");
  badgeWrap.append(statusBadge(detail.status));
  topRow.append(titleWrap, badgeWrap);
  top.append(topRow);
  root.append(top);

  const metadata = create("div", "detail-metadata");
  metadata.append(
    metadataCell("投递模式", modeNames[detail.mode] || detail.mode || "--"),
    metadataCell("SMTP 接受", `${detail.accepted_count || 0} / ${detail.recipient_count || 0}`),
    metadataCell("拒绝 / 错误", detail.refused_count ? `${detail.refused_count} 位被拒绝` : detail.error_code || "无"),
  );
  root.append(metadata);

  const recipientArea = create("section", "recipient-area");
  recipientArea.append(create("h3", "", "脱敏收件人"));
  const recipientList = create("div", "recipient-list");
  (detail.recipients || []).forEach((recipient) => {
    const item = create("span", `recipient-item ${statusClass(recipient.status)}`);
    item.append(create("span", "", recipient.address || recipient.domain || "(unknown)"), create("span", "recipient-status", statusNames[recipient.status] || recipient.status || "未知"));
    recipientList.append(item);
  });
  if (!recipientList.childElementCount) recipientList.append(create("span", "recipient-item", "没有收件人信息"));
  recipientArea.append(recipientList);
  root.append(recipientArea);

  const recipient = state.selectedItem?.recipient;
  if (state.folder === "inbox" && recipient) {
    const actions = create("div", "detail-actions");
    const read = create("button", "quiet-button", recipient.is_read ? "标为未读" : "标为已读");
    read.type = "button";
    read.addEventListener("click", () => { void updateMailboxState({ is_read: !recipient.is_read }); });
    const star = create("button", "quiet-button", recipient.is_starred ? "取消收藏" : "收藏副本");
    star.type = "button";
    star.addEventListener("click", () => { void updateMailboxState({ is_starred: !recipient.is_starred }); });
    const archive = create("button", "quiet-button", "归档副本");
    archive.type = "button";
    archive.addEventListener("click", () => { void updateMailboxState({ archived: true }); });
    actions.append(read, star, archive);
    root.append(actions);
  }

  const tabs = create("div", "detail-tabs");
  const tabSpecs = [["plain", "纯文本"], ["preview", "HTML 安全预览"], ["source", "清洗后源码"]];
  tabSpecs.forEach(([key, label]) => {
    const tab = create("button", `detail-tab${state.detailTab === key ? " active" : ""}`, label);
    tab.type = "button";
    tab.dataset.detailTab = key;
    tab.addEventListener("click", () => { state.detailTab = key; renderMessageDetail(state.selectedDetail); });
    tabs.append(tab);
  });
  root.append(tabs);
  const content = create("div", "detail-content");
  renderDetailContent(content, detail);
  root.append(content);
}

async function selectMessage(item) {
  state.selectedItem = item;
  state.detailTab = item.content_format === "html" ? "preview" : "plain";
  query(".mailbox-grid")?.classList.add("detail-open");
  renderMessageList(state.messages || { items: [] });
  const root = clear(byId("message-detail"));
  root.classList.add("open");
  root.append(create("div", "detail-empty", "正在加载邮件详情..."));
  if (window.matchMedia?.("(max-width: 760px)").matches) {
    root.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  try {
    const detail = await apiGet(`webui/message/${item.id}`);
    state.selectedDetail = detail;
    if (state.folder === "inbox" && item.recipient && !item.recipient.is_read) void updateMailboxState({ is_read: true }, true);
    renderMessageDetail(detail);
  } catch (error) {
    renderDetailEmpty();
    setNotice(error.message || "读取邮件详情失败。", "error");
  }
}

async function updateMailboxState(changes, silent = false) {
  const item = state.selectedItem;
  const recipient = item?.recipient;
  if (!item || !recipient) return;
  try {
    const values = await apiPost("webui/mailbox-state", { message_id: item.id, recipient_token: recipient.token, ...changes });
    Object.assign(recipient, values);
    if (state.selectedDetail) {
      const detailRecipient = state.selectedDetail.recipients?.find((candidate) => candidate.token === recipient.token);
      if (detailRecipient) Object.assign(detailRecipient, values);
    }
    renderMessageList(state.messages || { items: [] });
    if (changes.archived) {
      setNotice("本地投递副本已归档。", "", 3500);
      state.selectedItem = null;
      state.selectedDetail = null;
      renderDetailEmpty();
      await loadMailbox();
      return;
    }
    if (state.selectedDetail) renderMessageDetail(state.selectedDetail);
    if (!silent) setNotice("本地投递副本状态已更新。", "", 2800);
  } catch (error) {
    if (!silent) setNotice(error.message || "更新本地投递副本失败。", "error");
  }
}

function populateSettings(payload) {
  state.settings = payload;
  const settings = payload.settings || {};
  const checks = {
    "setting-enabled": "enabled",
    "setting-enable-owner-delivery": "enable_owner_delivery",
    "setting-enable-self-delivery": "enable_self_delivery",
    "setting-enable-admin-other-delivery": "enable_admin_other_delivery",
    "setting-private-self": "require_private_chat_for_self_delivery",
    "setting-enable-html": "enable_html_mail",
    "setting-sanitize-html": "sanitize_html_before_send",
    "setting-html-links": "html_allow_links",
    "setting-html-images": "html_allow_remote_images",
    "setting-history-enabled": "mail_history_enabled",
    "setting-history-content": "mail_history_store_content",
  };
  Object.entries(checks).forEach(([id, key]) => { byId(id).checked = Boolean(settings[key]); });
  const values = {
    "setting-html-domains": (settings.html_remote_image_allowed_domains || []).join("\n"),
    "setting-html-max": settings.max_html_body_chars,
    "setting-history-retention": settings.mail_history_retention_days,
    "setting-history-max": settings.mail_history_max_records,
    "setting-global-hour": settings.max_messages_per_hour,
    "setting-actor-success-hour": settings.max_successful_messages_per_actor_per_hour,
    "setting-actor-attempt-hour": settings.max_delivery_attempts_per_actor_per_hour,
    "setting-actor-cooldown": settings.actor_min_send_interval_seconds,
  };
  Object.entries(values).forEach(([id, value]) => { byId(id).value = value ?? ""; });
  const smtp = state.summary?.smtp || {};
  byId("credential-summary").textContent = `当前连接目标：${smtp.host || "未填写"}:${smtp.port || "--"} / ${String(smtp.security || "--").toUpperCase()}。账号、发件地址和 SMTP 授权码均不会从此接口返回。`;
  byId("settings-status").textContent = "已加载非敏感运行设置。";
}

async function loadSettings() {
  try {
    const settings = await apiGet("webui/settings");
    populateSettings(settings);
  } catch (error) {
    byId("settings-status").textContent = error.message || "读取安全配置失败。";
    setNotice(error.message || "读取安全配置失败。", "error");
  }
}

function splitLines(value) {
  return String(value || "").split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

function integerField(id, label) {
  const value = Number(settingsValue(id));
  if (!Number.isInteger(value)) throw new Error(`${label} 必须是整数。`);
  return value;
}

function readSettingsForm() {
  return {
    enabled: byId("setting-enabled").checked,
    enable_owner_delivery: byId("setting-enable-owner-delivery").checked,
    enable_self_delivery: byId("setting-enable-self-delivery").checked,
    enable_admin_other_delivery: byId("setting-enable-admin-other-delivery").checked,
    require_private_chat_for_self_delivery: byId("setting-private-self").checked,
    enable_html_mail: byId("setting-enable-html").checked,
    sanitize_html_before_send: byId("setting-sanitize-html").checked,
    html_allow_links: byId("setting-html-links").checked,
    html_allow_remote_images: byId("setting-html-images").checked,
    html_remote_image_allowed_domains: splitLines(settingsValue("setting-html-domains")),
    max_html_body_chars: integerField("setting-html-max", "HTML 源码上限"),
    mail_history_enabled: byId("setting-history-enabled").checked,
    mail_history_store_content: byId("setting-history-content").checked,
    mail_history_retention_days: integerField("setting-history-retention", "保留天数"),
    mail_history_max_records: integerField("setting-history-max", "最大记录数"),
    max_messages_per_hour: integerField("setting-global-hour", "全局每小时成功投递"),
    max_successful_messages_per_actor_per_hour: integerField("setting-actor-success-hour", "单用户每小时成功投递"),
    max_delivery_attempts_per_actor_per_hour: integerField("setting-actor-attempt-hour", "单用户每小时尝试"),
    actor_min_send_interval_seconds: integerField("setting-actor-cooldown", "同一用户冷却时间"),
  };
}

async function saveSettings() {
  const settings = readSettingsForm();
  const response = await apiPost("webui/settings", { settings });
  populateSettings(response.settings || state.settings);
  const suffix = response.restart_required ? " HTML 工具开关将在重载插件后生效。" : "";
  byId("settings-status").textContent = `已保存 ${response.changed?.length ?? 0} 项非敏感设置。${suffix}`;
  setNotice(`安全配置已保存。${suffix}`, "", 4500);
  await refreshAll();
}

async function probeSmtp() {
  const buttons = [byId("probe-smtp"), byId("settings-probe-smtp")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const result = await apiPost("webui/smtp-probe", {});
    setConnection("is-ready", "连接成功");
    setNotice(result.message || "SMTP 连接与登录测试成功，未发送邮件。", "", 5000);
  } catch (error) {
    setConnection("is-error", "连接失败");
    setNotice(error.message || "SMTP 连接测试失败。请检查账号和授权码。", "error");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function askClearHistory() {
  const dialog = byId("confirm-dialog");
  byId("confirm-title").textContent = "清空本地邮件历史";
  byId("confirm-copy").textContent = "这会删除本插件本地 SQLite 归档中的所有投递记录和投递副本状态。已发送到邮箱服务商的邮件不会被撤回。";
  dialog.showModal();
  return new Promise((resolve) => {
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "confirm");
    };
    dialog.addEventListener("close", onClose);
  });
}

async function clearHistory() {
  const confirmed = await askClearHistory();
  if (!confirmed) return;
  const button = byId("clear-history");
  button.disabled = true;
  try {
    const result = await apiPost("webui/history-clear", { confirm: "clear-mail-history" });
    state.messages = null;
    state.selectedItem = null;
    state.selectedDetail = null;
    renderDetailEmpty();
    setNotice(`已清空 ${result.removed ?? 0} 条本地邮件历史。`, "", 4200);
    await refreshAll();
  } catch (error) {
    setNotice(error.message || "清空本地邮件历史失败。", "error");
  } finally {
    button.disabled = false;
  }
}

function preferredTheme() {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch (_) {
    // A blocked storage implementation should not prevent the dashboard from loading.
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function setTheme(theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  all(".theme-option").forEach((element) => element.setAttribute("aria-pressed", String(element.dataset.theme === theme)));
  if (persist) {
    try { window.localStorage.setItem(THEME_KEY, theme); } catch (_) { /* ignored */ }
  }
}

async function refreshAll() {
  try {
    await loadOverview();
    if (state.activeView === "mailbox") await loadMailbox();
    if (state.activeView === "settings") await loadSettings();
  } catch (error) {
    setConnection("is-error", "读取失败");
    setNotice(error.message || "无法读取 MailRelay 状态。", "error");
  }
}

function handleResponsiveMenu() {
  const sidebar = byId("sidebar");
  const button = byId("mobile-nav-toggle");
  const open = !sidebar.classList.contains("open");
  sidebar.classList.toggle("open", open);
  button.setAttribute("aria-expanded", String(open));
}

function attachEvents() {
  all(".nav-button").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  all(".theme-option").forEach((button) => button.addEventListener("click", () => setTheme(button.dataset.theme)));
  all("[data-open-mailbox]").forEach((button) => button.addEventListener("click", () => { showView("mailbox"); setFolder(button.dataset.openMailbox || "sent"); }));
  all(".folder-tab").forEach((button) => button.addEventListener("click", () => setFolder(button.dataset.folder)));
  byId("global-refresh").addEventListener("click", () => { void refreshAll(); });
  byId("mobile-refresh").addEventListener("click", () => { void refreshAll(); });
  byId("mobile-nav-toggle").addEventListener("click", handleResponsiveMenu);
  byId("messages-refresh").addEventListener("click", () => { void loadMailbox(); });
  byId("message-search-button").addEventListener("click", () => { state.offset = 0; void loadMailbox(); });
  byId("message-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); state.offset = 0; void loadMailbox(); } });
  byId("message-status").addEventListener("change", () => { state.offset = 0; void loadMailbox(); });
  byId("message-format").addEventListener("change", () => { state.offset = 0; void loadMailbox(); });
  byId("clear-filters").addEventListener("click", () => {
    byId("message-search").value = "";
    byId("message-status").value = "";
    byId("message-format").value = "";
    state.offset = 0;
    void loadMailbox();
  });
  byId("messages-previous").addEventListener("click", () => { void loadMailbox(Math.max(0, state.offset - PAGE_SIZE)); });
  byId("messages-next").addEventListener("click", () => { void loadMailbox(state.offset + PAGE_SIZE); });
  byId("probe-smtp").addEventListener("click", () => { void probeSmtp(); });
  byId("settings-probe-smtp").addEventListener("click", () => { void probeSmtp(); });
  byId("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void saveSettings().catch((error) => { byId("settings-status").textContent = error.message || "保存安全配置失败。"; setNotice(error.message || "保存安全配置失败。", "error"); });
  });
  byId("clear-history").addEventListener("click", () => { void clearHistory(); });
  byId("setting-history-content").addEventListener("change", (event) => {
    if (event.target.checked) byId("settings-status").textContent = "注意：保存后，后续 SMTP 已接受邮件的主题、正文和已清洗 HTML 会保存在本机 SQLite 历史库中。";
  });
}

async function initialize() {
  setTheme(preferredTheme(), false);
  attachEvents();
  if (pluginBridge) {
    try { await pluginBridge.ready(); } catch (error) { throw new Error(error?.message || "无法连接 AstrBot Dashboard 页面桥接。 "); }
  } else {
    setNotice("当前显示演示数据。安装到 AstrBot Dashboard 后会自动读取受登录保护的本地历史。", "warning");
  }
  await refreshAll();
  if (state.demo) setConnection("is-ready", "演示模式");
}

initialize().catch((error) => {
  console.error(error);
  setConnection("is-error", "初始化失败");
  setNotice(error.message || "邮件中心初始化失败。", "error");
});
