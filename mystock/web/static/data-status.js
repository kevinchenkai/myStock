/* Read-only provenance display; times stay in their source/UTC zone. */
const cacheLabels = {ok:'采集成功', partial:'部分数据缺失', empty:'本次未返回数据', error:'采集失败', unsupported:'来源暂不支持', unknown:'无法判断', current:'已覆盖最近收盘日', stale:'缓存滞后', pending:'当日尚未确认收盘', awaiting_final_data:'等待收盘后采集', cached:'已有缓存'};
function cacheLabel(status) { return cacheLabels[status] || '无法判断'; }
function cacheTime(value) {
  if (!value) return '未知';
  const known = /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
  return esc(value.replace('T',' ').replace(/(?:Z|\+00:00)$/, ' UTC')) + (known ? '' : '（时区未知）');
}
function cacheNotice(status) { return ['ok','current','cached','pending'].includes(status) ? '' : ' cache-warning'; }
function cacheDaily(d) {
  return `<div class="cache-state${cacheNotice(d.status)}"><b>日线 · ${esc(cacheLabel(d.status))}</b>
    <p>行情日期 ${esc(d.data_as_of || '未知')} · 应覆盖收盘日 ${esc(d.expected_session || '无法判断')}</p>
    <p>该股采集 ${cacheTime(d.collected_at)} · 最近来源尝试 ${esc(cacheLabel(d.attempt_status))}（${d.scope === 'stock' ? '该股' : '来源汇总，不代表逐股成功'}）</p><details><summary>yfinance 日线采集说明</summary><p>最近尝试 ${cacheTime(d.last_attempt_at)} · 最近成功 ${cacheTime(d.last_success_at)}</p></details></div>`;
}
async function loadDataStatus() {
  const root = document.getElementById('data-status');
  if (!root) return;
  try {
    const data = await getJSON('/api/data-status');
    const warnings = data.sources.filter(s => s.status !== 'ok').length;
    const stockWarnings = data.stocks.filter(s => !['current','pending'].includes(s.daily.status) || s.snapshot.status !== 'ok' || s.snapshot.freshness !== 'cached').length;
    root.querySelector('summary').textContent = `数据状态 · ${warnings || stockWarnings ? warnings + ' 类来源 / ' + stockWarnings + ' 只股票需留意' : '查看各来源与个股缓存'}`;
    root.querySelector('.cache-body').innerHTML = `<p class="muted">采集结果是来源汇总；个股缓存可能更早。全部内容来自本地数据库。</p>
      <div class="cache-source-grid">${data.sources.map(s => `<article class="cache-state${cacheNotice(s.status)}"><b>${esc(s.label)} · ${esc(cacheLabel(s.status))}</b>
      <p>来源内最新数据 ${esc(s.data_as_of || '业务时间未知')}</p><p>最近尝试 ${cacheTime(s.last_attempt_at)}</p><p>最近成功 ${cacheTime(s.last_success_at)}</p></article>`).join('')}</div>
      <details><summary>个股数据日期与快照状态（${data.stocks.length}）</summary><div class="cache-source-grid">${data.stocks.map(s => `<article class="cache-state"><b>${esc(s.code)}</b>
      <p>日线 ${esc(s.daily.data_as_of || '未知')} · ${esc(cacheLabel(s.daily.status))}</p>
      <p>快照 ${esc(cacheLabel(s.snapshot.status))} · ${esc(cacheLabel(s.snapshot.freshness))}</p></article>`).join('')}</div></details>
      ${data.truncated ? '<p>仅展示前 400 个标的，请在个股详情查看其余标的。</p>' : ''}`;
  } catch (_) {
    root.querySelector('summary').textContent = '数据状态 · 暂时不可用';
    root.querySelector('.cache-body').textContent = '未能读取数据状态，请稍后重试。';
  }
}
function renderCacheSnapshot(data) {
  const s=data.snapshot, v=s.values;
  const suspension = v.suspension === 1 ? '停牌' : v.suspension === 0 ? '未停牌' : '未知';
  const secStatus = String(v.sec_status || '').split('.').pop() === 'NORMAL' ? '正常' : '未知';
  const sourceZone = {'Asia/Hong_Kong':'香港时间','America/New_York':'美东时间'}[s.source_timezone] || '时区未知';
  const rows=[['开盘',v.open_price],['日内最高',v.high_price],['日内最低',v.low_price],['昨收',v.prev_close_price],['量比',v.volume_ratio],['每手股数',v.lot_size]];
  return cacheDaily(data.daily) + `<section class="cache-card">
    <div class="cache-heading"><h3>Futu 缓存快照</h3><span>${esc(s.currency)}</span></div>
    <p class="cache-state${cacheNotice(s.status)}">${esc(cacheLabel(s.status))} · ${esc(cacheLabel(s.freshness))}${s.reason ? ' · '+esc(s.reason) : ''}</p>
    ${s.migration_required ? '<p class="cache-warning">快照展示需要维护者更新数据库结构并采集数据。</p>' : ''}
    <div class="cache-price">${s.has_cache ? fmtNum(v.last_price) : '—'} <small class="${s.change === null ? '' : plClass(s.change)}">${s.change === null ? '涨跌未知' : fmtSigned(s.change)+' ('+fmtSigned(s.change_pct)+'%)'}</small></div>
    <p class="muted">来源时间 ${esc(s.data_as_of || '未知')} <span class="cache-zone">${sourceZone}</span><br>采集时间 ${cacheTime(s.collected_at)}</p>
    <dl class="cache-values">${rows.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${label === '每手股数' ? fmtInt(value) : fmtNum(value)}</dd></div>`).join('')}
    <div><dt>停牌状态</dt><dd>${suspension}</dd></div><div><dt>证券状态</dt><dd>${secStatus}</dd></div></dl>
    <details><summary>来源与采集说明</summary><p>最近尝试 ${cacheTime(s.last_attempt_at)}<br>最近完整成功 ${cacheTime(s.last_success_at)}</p>
    <p>供应商证券状态 ${esc(v.sec_status || '未知')}。当前向上报价档位间隔 ${fmtNum(v.price_spread)}；仅为本次观察值，不用于历史交易规则。此卡为缓存，不是实时行情。</p></details>
    </section><div class="cache-state${cacheNotice(data.profile.status)}"><b>yfinance 公司资料 · ${esc(cacheLabel(data.profile.status))}</b><p>该股采集 ${cacheTime(data.profile.collected_at)} · 业务截至时间未知</p><p>最近来源尝试 ${cacheTime(data.profile.last_attempt_at)} · 最近来源成功 ${cacheTime(data.profile.last_success_at)}（${data.profile.scope === 'stock' ? '该股' : '来源汇总'}）</p></div>`;
}
async function loadCacheSnapshot(code) {
  const host = document.getElementById('detail-cache');
  try {
    const data=await getJSON(`/api/stock/${encodeURIComponent(code)}/snapshot`);
    if (host && host.isConnected) host.innerHTML=renderCacheSnapshot(data);
  } catch (_) { if(host && host.isConnected) host.textContent='缓存状态暂时不可用，其他已入库数据仍可查看。'; }
}
loadDataStatus();
