'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
let controller=null,seq=0,chart=null,candles=null,lines=[],reviewRows=[],comparison=null,paramSource='user_input';
const labels={pending:'pending / 待目标日完成',expired:'expired / 已过截止',unavailable:'unavailable / 无有效预测',skipped_in_session:'盘中（含午休）',missing_daily:'缺日线',missing_prediction:'缺有效预测',missing_bars:'缺完整小时线',awaiting_final_data:'等待收盘数据确认',ok:'可复盘'};
async function get(url,signal){const r=await fetch(url,{signal});const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j;}
function reviewStatus(row) {
  if (row.status === 'missing_prediction' && $('source').value === 'live') {
    return row.has_recomputed ? '已有历史重建；当前仅看 live' : '无当时生成的预测';
  }
  return labels[row.status] || row.status;
}
function showHistoryMode() {
  const historical = $('source').value === 'recomputed';
  $('history-mode').textContent = historical
    ? '历史回溯包含离线重建预测，逐日标注来源；不代表当时已生成或发布。'
    : '仅查看当时生成的预测。没有时间证据的旧记录不能补成 live；可切换到历史重建回溯。';
  $('use-recomputed').hidden = historical;
}
function params(){const p=new URLSearchParams();for(const el of $('scenario').querySelectorAll('input,select'))if(el.value.trim())p.set(el.id,el.value.trim());p.set('parameter_source',paramSource);return p;}
async function load(simulate=false){if(controller)controller.abort();controller=new AbortController();const signal=controller.signal,id=++seq,p=params();showHistoryMode();$('status').className='';$('status').textContent='正在读取隔离数据库…';
 try{const tasks=[get('/api/ml/v2/latest?source=live',signal),get('/api/ml/v2/review?'+p,signal)];if(simulate)tasks.push(get('/api/ml/v2/compare?'+p,signal));const result=await Promise.all(tasks);if(id!==seq)return;
 $('latest').innerHTML=result[0].results.map(r=>{const q=r.prediction;return `<article class="card"><strong>${esc(r.code)}</strong><span class="tag">${esc(labels[r.status]||r.status)}</span><p>${q?`风险覆盖区间 ${num(q.l_hat)} – ${num(q.h_hat)}`:'暂无满足时间证据的预测'}</p><small>as_of ${esc(q?.as_of||r.market_state.as_of)} · target ${esc(q?.target_session||r.market_state.target_session)}<br>published ${esc(q?.published_at)}<br>旧审计版本 ${r.audit_versions} 条</small></article>`;}).join('');
 reviewRows=result[1].results[0].rows;comparison=simulate?result[2]:null;renderReview();renderCompare();$('status').textContent=`${p.get('codes')} · ${reviewRows.length} 个市场 session · 可复盘 ${reviewRows.filter(r=>r.status==='ok').length}/${reviewRows.length} · ${simulate?'模拟完成':'行情复盘已载入；填写参数后模拟'}`;
 }catch(e){if(e.name==='AbortError')return;if(id!==seq)return;$('status').className='error';$('status').textContent=e.message;$('compare').innerHTML='';$('review').innerHTML='';destroyChart();}}
function renderReview(){$('review').innerHTML=[...reviewRows.entries()].reverse().map(([i,r])=>{const p=r.prediction,d=r.daily;return `<tr><td><button type="button" data-row="${i}">${esc(r.date)}</button></td><td>${esc(p?.as_of)}</td><td>${esc(reviewStatus(r))}${r.hourly_sources?.includes('futu_none')?'<br><small class="muted">小时线：Futu · 不复权</small>':''}</td><td>${esc(r.prediction_status==='recomputed'?'历史重建':r.prediction_status==='available'?'当时生成':r.has_recomputed?'历史重建（未选用）':'无预测')}</td><td>${num(p?.l_hat)} / ${num(p?.h_hat)}</td><td>${num(d?.low)} / ${num(d?.high)}</td><td>${r.hit==null?'—':r.hit?'是':'否'}</td></tr>`;}).join('');$('review').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>selectDate(Number(b.dataset.row))));mountChart();$('orders').textContent='未选中日期。';$('selected').textContent='风险边界不等于固定政策的可执行报价。';}
function renderCompare(){if(!comparison){$('compare').innerHTML='';return;}const r=comparison.results[0];$('compare').innerHTML=`<section class="panel"><h2>固定策略对照 · ${esc(r.currency)}</h2><p class="muted">${esc(comparison.note)} 参数来源：${esc(paramSource)}</p><div class="scroll"><table><thead><tr><th>Policy</th><th>期末权益</th><th>损益</th><th>费用口径</th><th>现金峰值占用</th><th>最大回撤</th><th>未平库存</th><th>歧义 / 缺口 session</th></tr></thead><tbody>${r.policies.map(p=>{const s=p.summary;return `<tr><td>${esc(p.policy)}</td><td>${num(s.equity)}</td><td class="${s.pnl>=0?'positive':'negative'}">${num(s.pnl)}</td><td>${esc(s.fee_status)} (${num(s.fees)})</td><td>${num(s.peak_cash_used)}</td><td>${num(s.max_drawdown*100)}%</td><td>${num(s.inventory)}</td><td>${s.ambiguous_sessions} / ${s.missing_sessions}</td></tr>`;}).join('')}</tbody></table></div><p class="muted">分红未核实付款日时列应收，不能用于买入。缺行情保留库存并标记旧价；同小时双触达显示保守次序假设。</p></section>`;}
function destroyChart(){if(chart)chart.remove();chart=null;candles=null;lines=[];}
function themeColor(token) { return getComputedStyle(document.documentElement).getPropertyValue(token).trim(); }
function applyChartTheme() {
  if (!chart || !candles) return;
  chart.applyOptions({
    layout: {background: {color: themeColor('--panel')}, textColor: themeColor('--text-soft')},
    grid: {vertLines: {color: themeColor('--border')}, horzLines: {color: themeColor('--border')}},
    rightPriceScale: {borderColor: themeColor('--border-strong')},
    timeScale: {borderColor: themeColor('--border-strong')},
  });
  candles.applyOptions({upColor: themeColor('--up'), downColor: themeColor('--down'),
    wickUpColor: themeColor('--up'), wickDownColor: themeColor('--down'), borderVisible: false});
  for (const {line, token} of lines) line.applyOptions({color: themeColor(token)});
}
function mountChart() {
  destroyChart();
  if (!window.LightweightCharts) return;
  chart = LightweightCharts.createChart($('chart'), {autoSize: true, timeScale: {timeVisible: false}});
  candles = chart.addCandlestickSeries ? chart.addCandlestickSeries() : chart.addSeries(LightweightCharts.CandlestickSeries);
  applyChartTheme();
  candles.setData(reviewRows.filter(r => r.daily).map(r => ({time: r.date, open: r.daily.open, high: r.daily.high, low: r.daily.low, close: r.daily.close})));
  chart.timeScale().fitContent();
}
// Restyle in place so switching themes preserves zoom and selected price lines.
const themeObserver = new MutationObserver(applyChartTheme);
themeObserver.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
systemTheme.addEventListener('change', applyChartTheme);
async function selectDate(i){const r=reviewRows[i],id=seq,p=r.prediction;for(const {line} of lines)candles?.removePriceLine(line);lines=[];const overlay=(price,title,token)=>{if(price&&candles)lines.push({line:candles.createPriceLine({price,color:themeColor(token),lineWidth:2,lineStyle:2,axisLabelVisible:true,title}),token});};overlay(p?.l_hat,'风险下界 '+r.date,'--accent');overlay(p?.h_hat,'风险上界 '+r.date,'--accent');const row=comparison?.results[0].policies[0].rows.find(x=>x.date===r.date);overlay(row?.buy_quote,'模拟买价','--down');overlay(row?.sell_quote,'模拟卖价','--up');$('selected').textContent=`${r.date} · prediction_id ${p?.prediction_id||'无'} · ${row?.ambiguity?'同 bar 双触达：保留歧义':'无双触达证据'} · ${r.price_basis} · 小时线 ${r.hourly_sources?.includes('futu_none')?'Futu · 不复权':'Yahoo'}${row?` · 现金 ${num(row.cash)} / 库存 ${num(row.inventory)} / 权益 ${num(row.equity)} · ${row.events.map(e=>e.kind+(e.side?' '+e.side:'')+(e.price?' '+num(e.price):'')).join('；')}`:''}`;
 const q=params();q.set('selected',r.date);try{const j=await get('/api/ml/v2/review?'+q,controller.signal);if(id!==seq||$('selected').textContent.indexOf(r.date)!==0)return;const facts=j.results[0].rows.find(x=>x.date===r.date).orders||[];$('orders').innerHTML=facts.length?`<div class="scroll"><table><thead><tr><th>时间</th><th>方向</th><th>价格 / 数量</th><th>快照状态</th><th>预测在委托前已发布</th></tr></thead><tbody>${facts.map(o=>`<tr><td>${esc(o.create_time)}</td><td>${esc(o.trd_side)}</td><td>${num(o.price)} / ${num(o.qty)}</td><td>${esc(o.order_status)} · snapshot_only</td><td>${o.prediction_available_at_order?'有证据':'无证据'}</td></tr>`).join('')}</tbody></table></div><p>仅事实，不计算假设收益。</p>`:'此日无已采集委托事实；不推断没有交易。';}catch(e){if(e.name!=='AbortError')$('orders').textContent=e.message;}}
$('use-recomputed').addEventListener('click',()=>{$('source').value='recomputed';load(false);});
$('scenario').addEventListener('submit',e=>{e.preventDefault();load(true);});
$('fixture').addEventListener('click',()=>{const hk=$('codes').value.startsWith('HK');const values={initial_cash:20000,initial_inventory:0,order_qty:hk?100:10,max_inventory:hk?500:50,max_holding:20,lot_size:hk?100:1,fee_bps:10,fee_flat:1};for(const [k,v] of Object.entries(values))$(k).value=v;paramSource='synthetic_fixture';$('scenario-source').textContent='已填入合成测试账户 / 合成正费用。HK lot=100 是测试规则，未声称为该证券实际历史规则。';});
for(const k of ['codes','days','end','source'])$(k).addEventListener('change',()=>load(false));
$('scenario').addEventListener('input',()=>{if(controller)controller.abort();seq++;comparison=null;$('compare').innerHTML='';$('status').textContent='参数已改变，请重新模拟。';if(paramSource==='synthetic_fixture')$('scenario-source').textContent='已修改合成场景；仍不是实盘账户设置。';});
window.addEventListener('beforeunload',()=>{controller?.abort();themeObserver.disconnect();systemTheme.removeEventListener('change',applyChartTheme);destroyChart();});load(false);
