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
function offlineHistory(params) {
  const period=params.period || '7d', range={ '24h':86400,'7d':604800,'30d':2592000 }[period], now=Date.now()/1000, start=now-range;
  const accounts=[{key:'qq-main:10001',platform:'aiocqhttp',bot_id:'10001',nickname:'我的机器人',last_seen:now-36},{key:'qq-backup:10002',platform:'aiocqhttp',bot_id:'10002',nickname:'小号机器人',last_seen:now-36}];
  const selected=accounts.filter(a => !params.account || a.key===params.account), events=[], series=[];
  for (const a of selected) {
    const index=accounts.findIndex(item => item.key===a.key);
    const spans=[{start:now-3*86400,end:now-2*86400,state:'gap',reason:'监控未运行'},{start:now-6*3600,end:now-5*3600,state:'gap',reason:'插件重载期间没有观测'},{start:now-12*3600,end:now-11.8*3600,state:'suspect',reason:'短暂探测失败，未达到掉线阈值'}];
    [25*86400,18*86400,9*86400,6*86400,4*86400,1.5*86400,22*3600,7*3600,2*3600].forEach((offset,i) => {
      const from=now-offset-index*900, length=[360,960,300,1800,540,1200,720,2400,420][i]+index*180;
      const e={id:index*100+i+1,account:a.key,bot_id:a.bot_id,nickname:a.nickname,started_at:from,confirmed_at:from+180,ended_at:from+length,status:'recovered',reason:i%2 ? 'OneBot 客户端连接已断开' : '平台报告账号已离线',duration_seconds:Math.max(0,Math.min(now,from+length)-Math.max(start,from))};
      if(e.ended_at>=start && e.started_at<=now) events.push(e);
      spans.push({start:from,end:from+length,state:'offline',reason:e.reason});
    });
    spans.sort((a,b) => a.start-b.start); const segments=[]; let cursor=now-28*86400;
    for(const span of spans) { if(span.start>cursor) segments.push({start:cursor,end:span.start,state:'online',reason:''}); if(span.state!=='gap') segments.push(span); cursor=Math.max(cursor,span.end); }
    segments.push({start:cursor,end:now-36,state:'online',reason:''});
    series.push({...a,segments:segments.filter(s => s.end>start && s.start<now).map(s => ({...s,start:Math.max(start,s.start),end:Math.min(now,s.end)}))});
  }
  const step=period==='24h' ? 3600 : 86400, buckets=Array.from({length:range/step},(_,i) => ({start:start+i*step,end:start+(i+1)*step,offline_seconds:null,online_seconds:0,observed_seconds:0,incident_count:0}));
  for(const b of buckets) {
    for(const a of series) for(const s of a.segments) {
      const length=Math.max(0,Math.min(b.end,s.end)-Math.max(b.start,s.start));
      if(!length || !['online','offline'].includes(s.state)) continue;
      b.observed_seconds+=length;
      if(s.state==='online') b.online_seconds+=length; else b.offline_seconds=(b.offline_seconds || 0)+length;
    }
    if(b.observed_seconds && b.offline_seconds==null) b.offline_seconds=0;
    b.incident_count=events.filter(e => e.started_at<b.end && e.ended_at>b.start).length;
  }
  const observed=buckets.reduce((v,b) => v+b.observed_seconds,0), online=buckets.reduce((v,b) => v+b.online_seconds,0);
  events.sort((a,b) => b.started_at-a.started_at);
  return {available:true,period,account:params.account || '',start,end:now,last_observed_at:selected.length ? now-36 : null,retention_days:90,accounts,series,incidents:events,incident_total:events.length,buckets,summary:{incident_count:events.length,observed_seconds:observed,offline_seconds:observed-online,longest_seconds:Math.max(0,...events.map(e => e.duration_seconds)),uptime_ratio:observed ? online/observed : null,coverage_ratio:selected.length ? observed/(range*selected.length) : 0}};
}
function demoGet(endpoint, params) {
  if (endpoint === "webui/summary") return summary();
  if (endpoint === "webui/settings") return settingsPayload();
  if (endpoint === "webui/offline-status") return offline();
  if (endpoint === "webui/offline-history") return offlineHistory(params);
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
