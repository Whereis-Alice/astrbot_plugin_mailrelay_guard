import test from 'node:test';
import assert from 'node:assert/strict';
// The presentation modules only need a resolved logo URL at import time.
globalThis.document={querySelector:() => ({src:'https://example.invalid/logo.svg'})};
globalThis.window={};
const charts=await import('../pages/mail-center/offline-charts.js');
const api=await import('../pages/mail-center/api.js');

test('all supported ranges have finite SVG geometry and matching bucket counts',async () => {
  for(const [period,count] of [['24h',24],['7d',7],['30d',30]]) {
    const data=await api.get('webui/offline-history',{period});
    assert.equal(data.buckets.length,count);
    const html=charts.historyContent(data);
    assert.equal((html.match(/class="chart-point"/g) || []).length,count);
    assert.doesNotMatch(html,/NaN|Infinity/);
    assert.match(html,/role="button" tabindex="0"/);
    assert.match(html,/时间轴/);
    assert.doesNotMatch(html,/没有可用于统计的监测数据/);
  }
});
test('unknown time breaks the line instead of becoming zero downtime',async () => {
  const data=await api.get('webui/offline-history',{period:'24h'});
  assert.ok(data.buckets.some(b => b.offline_seconds===null));
  const html=charts.historyContent(data);
  assert.match(html,/没有已判定的监测数据/);
  assert.ok((html.match(/class="chart-line"/g) || []).length>1);
});
test('account selection keeps the same demo history and sums across accounts',async () => {
  const all=await api.get('webui/offline-history',{period:'7d'});
  const one=await api.get('webui/offline-history',{period:'7d',account:'qq-main:10001'});
  const two=await api.get('webui/offline-history',{period:'7d',account:'qq-backup:10002'});
  assert.equal(one.series.length,1); assert.equal(two.series.length,1);
  assert.ok(Math.abs(all.summary.offline_seconds-one.summary.offline_seconds-two.summary.offline_seconds)<.1);
  assert.equal(all.summary.incident_count,one.summary.incident_count+two.summary.incident_count);
});
test('empty ranges are explicit and do not manufacture an uptime ratio',async () => {
  const data=await api.get('webui/offline-history',{period:'24h',account:'missing'});
  assert.equal(data.summary.uptime_ratio,null);
  const html=charts.historyContent(data);
  assert.match(html,/没有可用于统计的监测数据/);
  assert.match(html,/还没有账号记录/);
  assert.doesNotMatch(html,/NaN|Infinity/);
});
test('labels, reasons and filter values cannot inject markup',async () => {
  const data=await api.get('webui/offline-history',{period:'24h'});
  const evil='<img src=x onerror=alert(1)>"';
  data.series[0].nickname=evil; data.series[0].segments[0].reason=evil;
  data.incidents[0].reason=evil; data.accounts[0].key=evil;
  const html=charts.historyContent(data)+charts.historyShell('24h',evil,data.accounts)+charts.historyError(evil);
  assert.doesNotMatch(html,/<img src=x/);
  assert.match(html,/&lt;img/); assert.match(html,/&quot;/);
});
test('duration includes seconds in interval details and compact summaries',() => {
  assert.equal(charts.duration(0),'0 分钟');
  assert.equal(charts.duration(59),'59 秒');
  assert.equal(charts.duration(61),'1 分钟 1 秒');
  assert.equal(charts.duration(3661),'1 小时 1 分钟 1 秒');
  assert.equal(charts.duration(3661,true),'1 小时 1 分钟');
  assert.equal(charts.duration(-5),'0 分钟');
});
test('rebinding does not accumulate handlers, pointer/focus/tap share details',() => {
  const detail={innerHTML:''}, active={classList:{remove(){}}};
  const root={querySelector:() => detail,querySelectorAll:() => [active]};
  const target={dataset:{historyTitle:'确认掉线',historyLines:JSON.stringify(['60 秒','原因 <script>'])},classList:{add(){}},matches:() => true};
  const event={target:{closest:() => target},key:'Enter',preventDefault(){}};
  charts.bindHistory(root); const handler=root.onpointerover;
  charts.bindHistory(root); assert.notEqual(root.onpointerover,handler);
  root.onpointerover(event); assert.match(detail.innerHTML,/确认掉线/); assert.doesNotMatch(detail.innerHTML,/<script>/);
  detail.innerHTML=''; root.onfocusin(event); assert.match(detail.innerHTML,/60 秒/);
  detail.innerHTML=''; root.onclick(event); assert.match(detail.innerHTML,/60 秒/);
  root.onkeydown({...event,target,key:' '}); assert.match(detail.innerHTML,/60 秒/);
  root.onkeydown({...event,target,key:'Escape'}); assert.match(detail.innerHTML,/查看某个区间/);
});
