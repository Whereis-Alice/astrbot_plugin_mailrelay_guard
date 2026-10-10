// The AstrBot bridge handles authentication and plugin endpoint namespacing.
const bridge = window.AstrBotPluginPage;
export const demo = !bridge;
const copy = value => JSON.parse(JSON.stringify(value));
const template = (recovery = false) => `<div style="background:#f3f5f0;padding:28px;font-family:system-ui"><div style="max-width:520px;margin:auto;background:white;border-radius:16px;overflow:hidden"><div style="background:${recovery ? '#296a56' : '#b44949'};color:white;padding:24px;font-size:22px">${recovery ? '机器人已恢复' : '机器人掉线提醒'}</div><div style="padding:24px;line-height:1.9">机器人：{{nickname}}<br>账号：{{bot_id}}<br>平台：{{platform}}<br>时间：{{time}}<br>${recovery ? '本次掉线：{{downtime}}' : '原因：{{reason}}'}</div><div style="padding:15px 24px;background:#f7f8f5;color:#7c8980;font-size:12px">我的邮件 · 温柔提醒，安心投递</div></div></div>`;
let settings = {
  enabled:true,enable_owner_delivery:true,enable_self_delivery:true,enable_admin_other_delivery:true,require_private_chat_for_self_delivery:true,
  enable_html_mail:true,sanitize_html_before_send:true,html_allow_links:false,html_allow_remote_images:false,html_remote_image_allowed_domains:[],max_html_body_chars:30000,
  enable_attachments:true,allow_message_images:true,allow_message_files:true,allow_workspace_attachments:true,enable_inline_images:true,max_attachments_per_message:6,max_attachment_size_mb:10,max_total_attachment_size_mb:20,attachment_fetch_timeout_seconds:20,blocked_attachment_extensions:[".exe",".js"],
  mail_history_enabled:true,mail_history_store_content:true,mail_history_retention_days:30,mail_history_max_records:500,max_messages_per_hour:30,max_successful_messages_per_actor_per_hour:5,max_delivery_attempts_per_actor_per_hour:8,actor_min_send_interval_seconds:60,
  enable_offline_notifications:true,offline_check_interval_seconds:180,offline_failure_threshold:3,offline_notify_on_recovery:true,offline_notify_recipients:["owner@example.com"],offline_monitored_platforms:["aiocqhttp"],offline_subject_template:"【我的邮件】QQ 机器人掉线提醒：{{nickname}}",recovery_subject_template:"【我的邮件】QQ 机器人已恢复：{{nickname}}",offline_body_template:template(),recovery_body_template:template(true),
};
let items = [
  ["今天的工作摘要","submitted","html","self"], ["旅行照片，替你收好了","submitted","html","self"], ["项目进度通知","partial","plain","other"], ["周末读书清单","submitted","plain","owner"], ["邮件服务器连接失败","failed","plain","self"], ["本周待办事项","submitted","html","owner"],
].map(([subject,status,format,mode], i) => ({
  id:(i + 1).toString(16).padStart(32,"0"),created_at:new Date(Date.now() - i * 4100000).toISOString(),content_format:format,status,mode,subject,content_saved:status !== "failed",recipient_count:1,accepted_count:status === "failed" ? 0 : 1,refused_count:status === "partial" ? 1 : 0,attachment_count:i === 1 ? 1 : 0,inline_image_count:0,
  recipients:[{token:(i + 10).toString(16).padStart(24,"0"),address:"o***r@example.com",status:status === "failed" ? "failed" : "accepted",is_read:false,is_starred:false,archived:false}],
}));
function offline() { return {enabled:settings.enabled && settings.enable_offline_notifications,last_check_at:Date.now()/1000 - 36,last_error:"",effective_recipients:{count:settings.offline_notify_recipients.length || 1,source:settings.offline_notify_recipients.length ? "custom" : "owner"},monitored:settings.enable_offline_notifications ? [{key:"qq-main:10001",platform:"aiocqhttp",bot_id:"10001",nickname:"我的机器人",online:true,fail_count:0,offline_since:null,downtime:"",last_reason:""}] : []}; }
function summary() { return {readiness:"ready",configuration_problems:[],smtp:{host:"smtp.163.com",port:465,security:"ssl"},features:{history_enabled:settings.mail_history_enabled,history_store_content:settings.mail_history_store_content,history_available:true,llm_tools_registered:true,html_tools_registered:true,html_mail_enabled:settings.enable_html_mail,attachments_enabled:settings.enable_attachments,inline_images_enabled:true},history:{total:items.length,submitted:4,partial:1,failed:1,today_total:3,today_accepted:2},offline:offline()}; }
const settingsPayload = () => ({settings:copy(settings),restart_required_fields:["enable_html_mail"],secret_fields:["smtp_password","smtp_username","sender_address"]});
function demoGet(endpoint, params) {
  if (endpoint === "webui/summary") return summary();
  if (endpoint === "webui/settings") return settingsPayload();
  if (endpoint === "webui/offline-status") return offline();
  if (endpoint === "webui/messages") {
    let rows = items.filter(item => (!params.query || item.subject.includes(params.query)) && (!params.status || item.status === params.status) && (!params.format || item.content_format === params.format));
    if (params.folder === "errors") rows = rows.filter(item => ["failed","partial","unknown"].includes(item.status));
    if (params.folder === "inbox") rows = rows.filter(item => item.accepted_count && !item.recipients[0].archived).map(item => ({...item,recipient:item.recipients[0]}));
    const offset = params.offset || 0, limit = params.limit || 20;
    return {enabled:settings.mail_history_enabled,items:settings.mail_history_enabled ? copy(rows.slice(offset,offset+limit)) : [],total:rows.length,limit,offset,has_more:offset+limit < rows.length};
  }
  if (endpoint.startsWith("webui/message/")) {
    const item = items.find(value => value.id === endpoint.split("/").pop());
    if (!item) throw Error("未找到邮件");
    return {...copy(item),plain_body:item.content_saved ? "把重要的事，留在一封邮件里。\n\n今天的任务已经整理完成。明天继续，慢慢来，也会抵达。\n\n—— 我的邮件" : "",html_preview:item.content_saved ? '<div style="padding:36px;background:#edf2e8"><p style="letter-spacing:3px;color:#7e927e;font-size:11px">A LETTER FOR YOU</p><h1 style="color:#2c523b;font-size:26px">把重要的事，留在一封邮件里。</h1><p style="color:#667761;line-height:1.9">今天的任务已经整理完成。<br>明天继续，慢慢来，也会抵达。</p><hr style="border:0;border-top:1px solid #cad7c6;margin:25px 0"><small style="color:#82907c">我的邮件 · 每一封，都有着落。</small></div>' : "",attachment_names:item.attachment_count ? ["旅行照片.jpg"] : []};
  }
  throw Error("演示接口不存在");
}
function demoPost(endpoint, payload) {
  if (endpoint === "webui/settings") { settings = {...settings,...payload.settings}; return {changed:Object.keys(payload.settings),restart_required:!!("enable_html_mail" in payload.settings),settings:settingsPayload()}; }
  if (endpoint === "webui/smtp-probe") return {message:"演示：SMTP 连接测试成功。"};
  if (endpoint === "webui/offline-test") return {ok:true,message:"演示：通知测试成功，未发送真实邮件。",recipients:["owner@example.com"],failed:[]};
  if (endpoint === "webui/history-clear") { const removed = items.length; items = []; return {removed}; }
  if (endpoint === "webui/mailbox-state") { const item = items.find(i => i.id === payload.message_id); if (item) Object.assign(item.recipients[0],payload); return item?.recipients[0]; }
  throw Error("演示接口不存在");
}
function unwrap(value) { if (value?.status === "error") throw Error(value.message || "请求失败"); return value; }
export async function ready() {
  if (!bridge) return;
  let timeout;
  try { await Promise.race([bridge.ready(),new Promise((_,reject) => { timeout = setTimeout(() => reject(Error("连接 AstrBot 超时，请重新打开插件页面。")),15000); })]); }
  finally { clearTimeout(timeout); }
}
export async function get(endpoint, params = {}) { return unwrap(demo ? demoGet(endpoint,params) : await bridge.apiGet(endpoint,params)); }
export async function post(endpoint, body = {}) { return unwrap(demo ? demoPost(endpoint,body) : await bridge.apiPost(endpoint,body)); }
