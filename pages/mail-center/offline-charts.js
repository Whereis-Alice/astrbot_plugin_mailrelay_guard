import {esc,icon,empty,notice,button,date} from './ui.js';

export function duration(seconds,compact = false) {
  const value = Math.max(0,Math.round(Number(seconds) || 0));
  if (!value) return '0 分钟';
  if (value < 60) return value+' 秒';
  const days = Math.floor(value/86400), hours = Math.floor(value%86400/3600), minutes = Math.floor(value%3600/60);
  return [days ? days+' 天' : '',hours ? hours+' 小时' : '',minutes ? minutes+' 分钟' : '',!compact && value%60 ? value%60+' 秒' : ''].filter(Boolean).join(' ');
}
const stamp = value => date(value);
const shortStamp = (value,hour) => new Date(value*1000).toLocaleString('zh-CN',hour ? {hour:'2-digit',minute:'2-digit'} : {month:'numeric',day:'numeric'});
const names = {online:'在线',offline:'确认掉线',suspect:'疑似异常',unknown:'状态未知'};
const eventNames = {recovered:'已恢复',gap:'观测中断',unresolved:'未确认恢复'};
const ratio = value => value == null ? '—' : (value*100).toFixed(1)+'%';
const details = (title,lines) => '<strong>'+esc(title)+'</strong>'+lines.map(line => '<span>'+esc(line)+'</span>').join('');
const infoAttr = (title,lines) => 'data-history-title="'+esc(title)+'" data-history-lines="'+esc(JSON.stringify(lines))+'"';

function chart(data) {
  const buckets = data.buckets || [], max = Math.max(5,...buckets.map(b => (b.offline_seconds || 0)/60));
  const left=62,right=858,top=24,bottom=176, width=right-left, step=width/Math.max(1,buckets.length);
  const x = i => left+step*(i+0.5), y = b => bottom-((b.offline_seconds || 0)/60)/max*(bottom-top);
  const ticks = [0,0.5,1].map(p => '<line class="chart-grid" x1="'+left+'" x2="'+right+'" y1="'+(bottom-p*(bottom-top))+'" y2="'+(bottom-p*(bottom-top))+'"/><text class="chart-label" x="50" y="'+(bottom-p*(bottom-top)+4)+'" text-anchor="end">'+(max*p).toFixed(max<10 ? 1 : 0)+'</text>').join('');
  const runs=[]; let run=[];
  buckets.forEach((b,i) => { if (b.offline_seconds == null) { if(run.length) runs.push(run); run=[]; } else run.push([x(i),y(b)]); });
  if(run.length) runs.push(run);
  const paths=runs.map(points => {
    const d=points.map(([px,py],i) => (i ? 'L' : 'M')+px.toFixed(2)+','+py.toFixed(2)).join(' ');
    return '<path class="chart-area" d="'+d+' L'+points.at(-1)[0]+','+bottom+' L'+points[0][0]+','+bottom+' Z"/><path class="chart-line" d="'+d+'"/>';
  }).join('');
  const points=buckets.map((b,i) => {
    const lines=[stamp(b.start)+' → '+stamp(b.end),b.offline_seconds == null ? '没有已判定的监测数据' : '累计掉线：'+duration(b.offline_seconds)+' · 涉及 '+b.incident_count+' 次事件','已判定时长：'+duration(b.observed_seconds)];
    const label=lines.join('；');
    return '<g class="chart-point" role="button" tabindex="0" aria-label="'+esc(label)+'" '+infoAttr('这个区间的连接情况',lines)+'><title>'+esc(label)+'</title><rect class="chart-hit" x="'+(left+i*step)+'" y="'+top+'" width="'+step+'" height="'+(bottom-top+6)+'"/><line class="chart-cursor" x1="'+x(i)+'" x2="'+x(i)+'" y1="'+top+'" y2="'+bottom+'"/>'+(b.offline_seconds == null ? '<path class="chart-missing" d="M'+(x(i)-3)+','+bottom+' l6,0"/>' : '<circle class="chart-dot" cx="'+x(i)+'" cy="'+y(b)+'" r="4"/>')+'</g>';
  }).join('');
  const labels=buckets.filter((_,i) => i===0 || i===buckets.length-1 || i%Math.ceil(buckets.length/5)===0).map(b => {
    const i=buckets.indexOf(b);
    return '<text class="chart-label" x="'+x(i)+'" y="206" text-anchor="middle">'+esc(shortStamp(b.start,data.period==='24h'))+'</text>';
  }).join('');
  return '<div class="history-chart"><svg viewBox="0 0 880 222" role="group" aria-label="各区间累计掉线时长折线，单位分钟"><text class="chart-label" x="14" y="14">分钟</text>'+ticks+paths+points+labels+'</svg></div>';
}
function timelines(data) {
  const range=data.end-data.start;
  if (!data.series?.length) return empty('还没有账号记录','开启守护并完成检测后，连接时间轴会自动出现。','pulse');
  return '<div class="timeline-list">'+data.series.map(account => '<div class="timeline-row"><div class="timeline-identity"><strong>'+esc(account.nickname || account.bot_id || account.key)+'</strong><small>'+esc(account.platform)+' · '+esc(account.bot_id || account.key)+'</small></div><div class="timeline-track" aria-label="'+esc(account.nickname || account.key)+'连接时间轴">'+account.segments.map(s => {
    const left=Math.max(0,(s.start-data.start)/range*100),width=Math.max(0,(s.end-s.start)/range*100);
    const lines=[stamp(s.start)+' → '+stamp(s.end),'持续 '+duration(s.end-s.start),s.reason || (s.state==='online' ? '平台报告在线' : '没有可判定的连接状态')];
    return '<button type="button" class="timeline-segment '+esc(s.state)+'" style="left:'+left.toFixed(5)+'%;width:'+width.toFixed(5)+'%" '+infoAttr(names[s.state] || '未知',lines)+' aria-label="'+esc((names[s.state] || '未知')+'；'+lines.join('；'))+'"></button>';
  }).join('')+'</div></div>').join('')+'</div><div class="timeline-range"><span>'+esc(stamp(data.start))+'</span><span>'+esc(stamp(data.end))+'</span></div>';
}
function incidents(data) {
  if (!data.incidents?.length) return '<div class="history-no-events">'+icon('check')+' 这个范围内没有已确认的掉线事件。</div>';
  return '<div class="incident-list">'+data.incidents.map(e => '<details class="incident"><summary><span class="incident-marker '+(e.status==='recovered' ? 'recovered' : '')+'"></span><div class="grow"><strong>'+esc(e.nickname || e.bot_id || e.account)+'</strong><small>'+esc(stamp(e.started_at))+'</small></div><span class="incident-duration">'+esc(duration(e.duration_seconds))+'</span><span class="badge '+(e.status==='recovered' ? '' : 'warn')+'">'+esc(eventNames[e.status] || '未确认恢复')+'</span>'+icon('arrow')+'</summary><div class="incident-body"><p>原因：'+esc(e.reason || '连接中断')+'</p><p>确认掉线：'+esc(stamp(e.confirmed_at))+' · '+(e.status==='recovered' ? '恢复时间' : '最后观测')+'：'+esc(e.ended_at ? stamp(e.ended_at) : '尚未确认')+'</p><p>统计时长只计算当前筛选范围内已确认的掉线区间；未知状态和观测空白不计入。</p></div></details>').join('')+'</div>'+(data.incident_total>100 ? '<p class="monitor-note">只列出最近 100 次事件，统计包含范围内全部事件。</p>' : '');
}
export function historyContent(data) {
  const s=data.summary || {};
  const stats=[['掉线事件',s.incident_count+' 次','范围内发生或延续的已确认事件'],['累计掉线',duration(s.offline_seconds,true),'所有选中账号的累计时长'],['最长掉线',duration(s.longest_seconds,true),'范围内单次事件的已观测时长'],['在线占比',ratio(s.uptime_ratio),'仅在已判定的在线 / 离线时间中计算']];
  return (!s.observed_seconds ? notice('这个范围内没有可用于统计的监测数据。请先开启掉线守护并等待连续检测；已有未知状态可在时间轴中查看。') : '')+'<div class="history-stats">'+stats.map(([label,value,note]) => '<div><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong><small>'+esc(note)+'</small></div>').join('')+'</div><div class="history-section-head"><div><h3>掉线时长趋势</h3><p>每个区间的累计时长 · 悬停、聚焦或点击查看详情</p></div><span class="history-observed">观测覆盖 '+ratio(s.coverage_ratio)+'</span></div>'+chart(data)+'<div class="history-detail" id="history-detail" role="status" aria-live="polite">'+details('查看某个区间',['将指针移到折线，或用 Tab 聚焦；手机可以直接点选。'])+'</div><div class="history-section-head"><div><h3>账号连接时间轴</h3><p>绿色在线，红色掉线；空白不代表在线。</p></div><div class="timeline-legend">'+Object.entries(names).map(([key,name]) => '<span><i class="'+key+'"></i>'+esc(name)+'</span>').join('')+'<span><i class="gap"></i>无观测</span></div></div>'+timelines(data)+'<div class="history-section-head"><div><h3>最近的掉线事件</h3><p>展开查看原因、确认和恢复时间</p></div></div>'+incidents(data)+'<p class="monitor-note history-footnote">最后观测：'+esc(data.last_observed_at ? stamp(data.last_observed_at) : '暂无')+' · 历史在本地保存 '+(data.retention_days || 90)+' 天。检测间隔限制了时间精度；插件重启、暂停、长时间未检测的空白和未知状态不计入在线或掉线。趋势按滚动区间分组，不是自然日。</p>';
}
export function historyShell(period,account,accounts=[]) {
  return '<div class="card-head"><div><div class="eyebrow">CONNECTION INSIGHTS</div><h2>掉线概览</h2><p>不只看此刻，也看看连接最近过得怎么样。</p></div><div class="history-filters"><select id="history-period" aria-label="历史时间范围">'+[['24h','最近 24 小时'],['7d','最近 7 天'],['30d','最近 30 天']].map(([key,name]) => '<option value="'+key+'" '+(key===period ? 'selected' : '')+'>'+name+'</option>').join('')+'</select><select id="history-account" aria-label="筛选历史账号"><option value="">全部账号</option>'+accounts.map(a => '<option value="'+esc(a.key)+'" '+(a.key===account ? 'selected' : '')+'>'+esc((a.nickname || a.bot_id || a.key)+(a.nickname && a.bot_id ? ' · '+a.bot_id : ''))+'</option>').join('')+'</select>'+button('history-refresh','刷新','secondary small','refresh')+'</div></div><div id="history-alert" role="status"></div><div id="history-content" aria-busy="true">'+empty('正在读取连接历史','历史记录不会因插件重载而消失。','pulse')+'</div>';
}
export function historyError(message) { return notice(message,'warning')+button('history-refresh','重试','secondary small'); }
export function bindHistory(container) {
  const show = target => {
    if (!target?.dataset.historyTitle || !container.querySelector('#history-detail')) return;
    container.querySelectorAll('.history-active').forEach(el => el.classList.remove('history-active'));
    target.classList.add('history-active');
    let lines=[]; try { lines=JSON.parse(target.dataset.historyLines); } catch { /* Invalid DOM data. */ }
    container.querySelector('#history-detail').innerHTML=details(target.dataset.historyTitle,lines);
  };
  container.onpointerover = event => show(event.target.closest('[data-history-title]'));
  container.onfocusin = event => show(event.target.closest('[data-history-title]'));
  container.onclick = event => show(event.target.closest('[data-history-title]'));
  container.onkeydown = event => {
    if ((event.key==='Enter' || event.key===' ') && event.target.matches('.chart-point')) { event.preventDefault(); show(event.target); }
    if (event.key==='Escape' && container.querySelector('#history-detail')) { container.querySelectorAll('.history-active').forEach(el => el.classList.remove('history-active')); container.querySelector('#history-detail').innerHTML=details('查看某个区间',['悬停、聚焦或点击折线和时间轴，查看详细信息。']); }
  };
}
