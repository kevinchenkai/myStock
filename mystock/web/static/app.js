"use strict";

// ---------- 工具 ----------
// 涨跌配色：>0 红，<0 绿，=0 中性。全站统一走这里。
function plClass(v) {
  const n = Number(v);
  if (!isFinite(n) || n === 0) return "flat";
  return n > 0 ? "up" : "down";
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === "" || isNaN(Number(v))) return "—";
  return Number(v).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}
function fmtInt(v) {
  if (v === null || v === undefined || v === "" || isNaN(Number(v))) return "—";
  return Number(v).toLocaleString("en-US");
}
function plCell(v, suffix = "") {
  const n = Number(v);
  const txt = (v === null || v === undefined || isNaN(n))
    ? "—"
    : (n > 0 ? "+" : "") + fmtNum(v) + suffix;
  return `<td class="${plClass(v)}">${txt}</td>`;
}
function fmtSigned(v, suffix = "") {
  const n = Number(v);
  if (v === null || v === undefined || isNaN(n)) return "—";
  return (n > 0 ? "+" : "") + fmtNum(v) + suffix;
}
function fmtDays(v) {
  if (v === null || v === undefined || isNaN(Number(v))) return "—";
  return Number(v).toFixed(Number(v) < 10 ? 1 : 0) + " 天";
}
function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `请求失败 ${r.status}`);
  }
  return r.json();
}

// 主题切换逻辑已抽到共享的 theme.js（首页与复盘页共用）。

// ---------- Tab 切换 ----------
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    const tab = t.dataset.tab;
    document.getElementById("panel-positions").style.display = tab === "positions" ? "" : "none";
    document.getElementById("panel-trades").style.display = tab === "trades" ? "" : "none";
    document.getElementById("panel-pnl").style.display = tab === "pnl" ? "" : "none";
    if (tab === "trades") loadTrades();
    if (tab === "pnl") loadPnl();
  });
});

document.querySelectorAll(".subtab").forEach((s) => {
  s.addEventListener("click", () => {
    document.querySelectorAll(".subtab").forEach((x) => x.classList.remove("active"));
    s.classList.add("active");
    const sub = s.dataset.sub;
    document.getElementById("orders-table").style.display = sub === "orders" ? "" : "none";
    document.getElementById("deals-table").style.display = sub === "deals" ? "" : "none";
  });
});

// ---------- 状态 ----------
// 原始数据缓存（fetch 一次），筛选/排序在前端对缓存重渲染。
const state = {
  positions: { raw: [], snapshot: null, market: "", sort: { key: null, dir: 0 } },
  orders: { raw: [], market: "" },
  deals: { raw: [], market: "" },
  pnl: { raw: [], market: "", sort: { key: null, dir: 0 } },
};

function byMarket(rows, market) {
  return market ? rows.filter((r) => r.market === market) : rows;
}

// ---------- 市场筛选 ----------
document.querySelectorAll(".filter").forEach((f) => {
  const scope = f.dataset.filter; // positions / trades
  f.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      f.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      const market = chip.dataset.market;
      if (scope === "positions") {
        state.positions.market = market;
        renderPositions();
      } else if (scope === "pnl") {
        state.pnl.market = market;
        renderPnl();
      } else {
        state.orders.market = market;
        state.deals.market = market;
        renderOrders();
        renderDeals();
      }
    });
  });
});

// ---------- 持仓 ----------
// 列定义：key=字段，label=表头，num=是否数值列（可排序），cell=单元格渲染。
const POSITION_COLS = [
  { key: "code", label: "代码", text: true, num: false,
    cell: (p) => `<td class="text"><span class="clickable" data-code="${esc(p.code)}">${esc(p.code)}</span></td>` },
  { key: "name", label: "名称", text: true, num: false,
    cell: (p) => `<td class="text">${esc(p.name || "")}</td>` },
  { key: "market", label: "市场", text: true, num: false,
    cell: (p) => `<td class="text">${esc(p.market)}</td>` },
  { key: "qty", label: "持仓", num: true, cell: (p) => `<td>${fmtInt(p.qty)}</td>` },
  { key: "can_sell_qty", label: "可卖", num: true, cell: (p) => `<td>${fmtInt(p.can_sell_qty)}</td>` },
  { key: "cost_price", label: "成本价", num: true, cell: (p) => `<td>${fmtNum(p.cost_price)}</td>` },
  { key: "nominal_price", label: "市价", num: true, cell: (p) => `<td>${fmtNum(p.nominal_price)}</td>` },
  { key: "market_val", label: "市值", num: true, cell: (p) => `<td>${fmtNum(p.market_val)}</td>` },
  { key: "pl_val", label: "浮动盈亏", num: true, cell: (p) => plCell(p.pl_val) },
  { key: "pl_ratio", label: "盈亏比例", num: true, cell: (p) => plCell(p.pl_ratio, "%") },
  { key: "currency", label: "币种", text: true, num: false,
    cell: (p) => `<td class="text">${esc(p.currency || "")}</td>` },
];

async function loadPositions() {
  const wrap = document.getElementById("positions-table");
  try {
    const data = await getJSON("/api/positions");
    state.positions.raw = data.positions || [];
    state.positions.snapshot = data.snapshot_date;
    renderPositions();
  } catch (e) {
    wrap.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderPositions() {
  const meta = document.getElementById("positions-meta");
  const wrap = document.getElementById("positions-table");
  const st = state.positions;

  if (!st.raw.length) {
    meta.textContent = "";
    wrap.innerHTML = `<div class="empty">暂无持仓数据。请先运行 <code>bash scripts/init.sh</code>。</div>`;
    return;
  }

  let list = byMarket(st.raw, st.market);
  list = sortByNum(list, st.sort);   // dir: 1 升 / -1 降 / 0 原始顺序

  meta.textContent =
    `快照日期：${st.snapshot} · 显示 ${list.length} / ${st.raw.length} 支`;

  const ths = POSITION_COLS.map((c) => {
    const cls = (c.text ? "text " : "") + (c.num ? "sortable" : "");
    let arrow = "";
    if (c.num && st.sort.key === c.key && st.sort.dir !== 0) {
      arrow = `<span class="arrow">${st.sort.dir === 1 ? "▲" : "▼"}</span>`;
    }
    const attr = c.num ? ` data-sortkey="${c.key}"` : "";
    return `<th class="${cls.trim()}"${attr}>${c.label}${arrow}</th>`;
  }).join("");

  const rows = list.map((p) => `<tr>${POSITION_COLS.map((c) => c.cell(p)).join("")}</tr>`).join("");

  wrap.innerHTML = `<table><thead><tr>${ths}</tr></thead><tbody>${rows}</tbody></table>`;
  bindCodeClicks(wrap);
  bindSortHeaders(wrap, state.positions.sort, renderPositions);
}

// 表头点击：倒序 → 正序 → 取消，循环切换。
// sortState：{key,dir} 排序状态；rerender：重渲染回调。
function bindSortHeaders(scope, sortState, rerender) {
  scope.querySelectorAll("th.sortable[data-sortkey]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sortkey;
      const st = sortState;
      if (st.key !== key) {
        st.key = key; st.dir = -1;        // 新列：先倒序
      } else if (st.dir === -1) {
        st.dir = 1;                       // 倒序 → 正序
      } else if (st.dir === 1) {
        st.key = null; st.dir = 0;        // 正序 → 取消
      } else {
        st.dir = -1;                      // 取消 → 倒序
      }
      rerender();
    });
  });
}

// 通用：按数值列排序（dir: 1 升 / -1 降 / 0 原顺序），空值恒末尾。
function sortByNum(list, sort) {
  if (!sort.key || sort.dir === 0) return list;
  const { key, dir } = sort;
  return list.slice().sort((a, b) => {
    const x = Number(a[key]), y = Number(b[key]);
    const xn = isFinite(x), yn = isFinite(y);
    if (!xn && !yn) return 0;
    if (!xn) return 1;
    if (!yn) return -1;
    return (x - y) * dir;
  });
}

// ---------- 交易 ----------
let tradesLoaded = false;
async function loadTrades() {
  if (tradesLoaded) return;
  tradesLoaded = true;
  await Promise.all([loadOrders(), loadDeals()]);
}

function sideBadge(side) {
  const s = String(side || "").toUpperCase();
  if (s === "BUY") return `<span class="badge buy">买入</span>`;
  if (s === "SELL") return `<span class="badge sell">卖出</span>`;
  return esc(side || "");
}

async function loadOrders() {
  const wrap = document.getElementById("orders-table");
  try {
    state.orders.raw = await getJSON("/api/orders");
    renderOrders();
  } catch (e) {
    wrap.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderOrders() {
  const wrap = document.getElementById("orders-table");
  const list = byMarket(state.orders.raw, state.orders.market);
  if (!list.length) { wrap.innerHTML = `<div class="empty">暂无订单</div>`; return; }
  const rows = list.map((o) => `
      <tr>
        <td class="text"><span class="clickable" data-code="${esc(o.code)}">${esc(o.code)}</span></td>
        <td class="text">${esc(o.name || "")}</td>
        <td class="text">${esc(o.market || "")}</td>
        <td class="text">${sideBadge(o.trd_side)}</td>
        <td class="text">${esc(o.order_type || "")}</td>
        <td class="text">${esc(o.order_status || "")}</td>
        <td>${fmtNum(o.price)}</td>
        <td>${fmtInt(o.qty)}</td>
        <td>${fmtInt(o.dealt_qty)}</td>
        <td>${fmtNum(o.dealt_avg_price)}</td>
        <td class="text">${esc(o.create_time || "")}</td>
      </tr>`).join("");
  wrap.innerHTML = `
      <table>
        <thead><tr>
          <th class="text">代码</th><th class="text">名称</th><th class="text">市场</th><th class="text">方向</th>
          <th class="text">类型</th><th class="text">状态</th><th>价格</th>
          <th>委托量</th><th>成交量</th><th>成交均价</th><th class="text">下单时间</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  bindCodeClicks(wrap);
}

async function loadDeals() {
  const wrap = document.getElementById("deals-table");
  try {
    state.deals.raw = await getJSON("/api/deals");
    renderDeals();
  } catch (e) {
    wrap.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderDeals() {
  const wrap = document.getElementById("deals-table");
  const list = byMarket(state.deals.raw, state.deals.market);
  if (!list.length) { wrap.innerHTML = `<div class="empty">暂无成交</div>`; return; }
  const rows = list.map((d) => `
      <tr>
        <td class="text"><span class="clickable" data-code="${esc(d.code)}">${esc(d.code)}</span></td>
        <td class="text">${esc(d.name || "")}</td>
        <td class="text">${esc(d.market || "")}</td>
        <td class="text">${sideBadge(d.trd_side)}</td>
        <td>${fmtNum(d.price)}</td>
        <td>${fmtInt(d.qty)}</td>
        <td class="text">${esc(d.create_time || "")}</td>
        <td class="text">${esc(d.order_id || "")}</td>
      </tr>`).join("");
  wrap.innerHTML = `
      <table>
        <thead><tr>
          <th class="text">代码</th><th class="text">名称</th><th class="text">市场</th><th class="text">方向</th>
          <th>成交价</th><th>成交量</th><th class="text">成交时间</th><th class="text">关联订单</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  bindCodeClicks(wrap);
}

function bindCodeClicks(scope) {
  scope.querySelectorAll(".clickable[data-code]").forEach((el) => {
    el.addEventListener("click", () => openStock(el.dataset.code));
  });
}

function bindAnalysisClicks(scope) {
  scope.querySelectorAll(".clickable[data-analysis]").forEach((el) => {
    el.addEventListener("click", () => openAnalysis(el.dataset.analysis));
  });
}

// ---------- 交易盈亏（已实现）----------
// 列定义：num 列可排序；cur 列带币种后缀（本币计价）。
const PNL_COLS = [
  { key: "code", label: "代码", text: true,
    cell: (r) => `<td class="text"><span class="clickable" data-analysis="${esc(r.code)}" title="打开交易复盘">${esc(r.code)}</span></td>` },
  { key: "name", label: "名称", text: true, cell: (r) => `<td class="text">${esc(r.name || "")}</td>` },
  { key: "market", label: "市场", text: true, cell: (r) => `<td class="text">${esc(r.market || "")}</td>` },
  { key: "buy_qty", label: "买入量", num: true, cell: (r) => `<td>${fmtInt(r.buy_qty)}</td>` },
  { key: "sell_qty", label: "卖出量", num: true, cell: (r) => `<td>${fmtInt(r.sell_qty)}</td>` },
  { key: "avg_buy_price", label: "买入均价", num: true, cell: (r) => `<td>${fmtNum(r.avg_buy_price)}</td>` },
  { key: "avg_sell_price", label: "卖出均价", num: true, cell: (r) => `<td>${fmtNum(r.avg_sell_price)}</td>` },
  { key: "realized_pnl", label: "已实现盈亏", num: true, cell: (r) => plCell(r.realized_pnl) },
  { key: "realized_pnl_ratio", label: "盈亏率", num: true, cell: (r) => plCell(r.realized_pnl_ratio, "%") },
  { key: "currency", label: "币种", text: true, cell: (r) => `<td class="text">${esc(r.currency || "")}</td>` },
  { key: "last_deal_time", label: "最后成交", text: true,
    cell: (r) => `<td class="text">${esc((r.last_deal_time || "").slice(0, 19))}</td>` },
];

let pnlLoaded = false;
async function loadPnl() {
  if (pnlLoaded) return;
  pnlLoaded = true;
  const wrap = document.getElementById("pnl-table");
  try {
    const data = await getJSON("/api/pnl");
    state.pnl.raw = data.rows || [];
    renderPnl();
  } catch (e) {
    pnlLoaded = false;  // 失败允许重试
    wrap.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderPnl() {
  const meta = document.getElementById("pnl-meta");
  const wrap = document.getElementById("pnl-table");
  const st = state.pnl;

  if (!st.raw.length) {
    meta.textContent = "";
    wrap.innerHTML = `<div class="empty">暂无成交数据，无法计算交易盈亏。</div>`;
    return;
  }

  let list = byMarket(st.raw, st.market);
  list = sortByNum(list, st.sort);

  // 合计（按当前筛选）：分币种汇总已实现盈亏
  const sumByCcy = {};
  let uncovered = 0;
  list.forEach((r) => {
    const c = r.currency || "—";
    sumByCcy[c] = (sumByCcy[c] || 0) + Number(r.realized_pnl || 0);
    uncovered += Number(r.uncovered_sell_qty || 0);
  });
  const sumStr = Object.entries(sumByCcy)
    .map(([c, v]) => `${c} ${v >= 0 ? "+" : ""}${fmtNum(v)}`)
    .join(" · ");
  let metaTxt = `显示 ${list.length} / ${st.raw.length} 支 · 已实现合计：${sumStr}`;
  if (uncovered > 0) metaTxt += ` · ⚠ ${fmtInt(uncovered)} 股卖出无成本基准(未计入)`;
  meta.textContent = metaTxt;

  const ths = PNL_COLS.map((c) => {
    const cls = (c.text ? "text " : "") + (c.num ? "sortable" : "");
    let arrow = "";
    if (c.num && st.sort.key === c.key && st.sort.dir !== 0) {
      arrow = `<span class="arrow">${st.sort.dir === 1 ? "▲" : "▼"}</span>`;
    }
    const attr = c.num ? ` data-sortkey="${c.key}"` : "";
    return `<th class="${cls.trim()}"${attr}>${c.label}${arrow}</th>`;
  }).join("");

  const rows = list.map((r) => `<tr>${PNL_COLS.map((c) => c.cell(r)).join("")}</tr>`).join("");
  wrap.innerHTML = `<table><thead><tr>${ths}</tr></thead><tbody>${rows}</tbody></table>`;
  bindAnalysisClicks(wrap);   // 盈亏 Tab 代码 → 弹出交易复盘浮窗
  bindSortHeaders(wrap, st.sort, renderPnl);
}

// ---------- 个股详情下钻 ----------
const overlay = document.getElementById("overlay");
document.getElementById("detail-close").addEventListener("click", () => overlay.classList.remove("open"));
overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.classList.remove("open"); });

async function openStock(code) {
  overlay.classList.add("open");
  document.getElementById("detail-title").textContent = code;
  const body = document.getElementById("detail-body");
  body.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}`);
    document.getElementById("detail-title").textContent =
      d.name ? `${code} · ${d.name}` : code;
    body.innerHTML =
      section("通用信息", `<div id="detail-profile"><div class="empty">加载通用信息中…</div></div>`) +
      section("价格走势（收盘价）", renderChart(d.quotes)) +
      section(`历史日线（${d.quotes.length} 条）`, renderQuotesTable(d.quotes)) +
      section(`我的订单（${d.orders.length} 条）`, renderDetailOrders(d.orders)) +
      section(`我的成交（${d.deals.length} 条）`, renderDetailDeals(d.deals));
    bindSectionToggles(body);
    loadProfile(code);   // 通用信息走 yfinance 实时接口，异步填充
  } catch (e) {
    body.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

// ---------- 交易复盘浮窗（与个股详情共用 overlay）----------
async function openAnalysis(code) {
  overlay.classList.add("open");
  document.getElementById("detail-title").textContent = `${code} · 交易复盘`;
  const body = document.getElementById("detail-body");
  body.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}/analysis`);
    const a = d.analysis;
    const s = a.summary, st = a.stats;
    document.getElementById("detail-title").textContent =
      (s.name ? `${code} · ${s.name}` : code) + " · 交易复盘";
    body.innerHTML =
      renderAnalysisOverview(s, st) +
      section("交易盈亏分析", renderAnalysisSummary(a.observations, st, s)) +
      section(`已平仓回合（${a.round_trips.length}）`, renderRoundTrips(a.round_trips)) +
      section(`我的成单交易（${(d.deals || []).length} 笔）`, renderAnalysisDeals(d.deals || []));
    bindSectionToggles(body);
  } catch (e) {
    body.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderAnalysisOverview(s, st) {
  const cur = s.currency ? ` ${esc(s.currency)}` : "";
  const cards = [
    { label: "已实现盈亏", value: fmtSigned(s.realized_pnl) + cur, cls: plClass(s.realized_pnl), big: true },
    { label: "已平仓回合", value: fmtInt(st.closed_trips) },
    { label: "胜率", value: st.win_rate === null ? "—" : fmtNum(st.win_rate, 1) + "%",
      cls: st.win_rate === null ? "" : (st.win_rate >= 50 ? "up" : "down") },
    { label: "盈亏比", value: st.profit_factor === null ? "—" : fmtNum(st.profit_factor),
      cls: st.profit_factor === null ? "" : (st.profit_factor >= 1 ? "up" : "down"), hint: "总盈利 / 总亏损" },
    { label: "成交笔数", value: fmtInt(s.deal_count) },
    { label: "买入 / 卖出量", value: `${fmtInt(s.buy_qty)} / ${fmtInt(s.sell_qty)}` },
    { label: "买入均价", value: fmtNum(s.avg_buy_price) + cur },
    { label: "卖出均价", value: fmtNum(s.avg_sell_price) + cur },
    { label: "平均持有", value: fmtDays(st.avg_hold_days) },
    { label: "净持仓", value: fmtInt(s.net_qty) },
  ];
  const html = cards.map((c) => `
    <div class="metric ${c.big ? "metric-big" : ""}">
      <div class="metric-label">${esc(c.label)}${c.hint ? `<span class="metric-hint" title="${esc(c.hint)}"> ⓘ</span>` : ""}</div>
      <div class="metric-value ${c.cls || ""}">${c.value}</div>
    </div>`).join("");
  return `<div class="metric-grid">${html}</div>`;
}

function renderAnalysisSummary(observations, st, s) {
  if (!observations || !observations.length) return `<div class="empty">暂无可复盘的交易。</div>`;
  const icon = { good: "✅", warn: "⚠️", info: "•" };
  let summaryLine = "";
  if (st.closed_trips) {
    const cur = s.currency ? ` ${esc(s.currency)}` : "";
    const pnl = `<span class="${plClass(s.realized_pnl)}">${fmtSigned(s.realized_pnl)}${cur}</span>`;
    const verdict = s.realized_pnl > 0 ? "整体<strong>盈利</strong>"
      : s.realized_pnl < 0 ? "整体<strong>亏损</strong>" : "整体<strong>持平</strong>";
    summaryLine = `<p class="analysis-summary">共 ${st.closed_trips} 个已平仓回合，该股交易${verdict}，已实现盈亏 ${pnl}。</p>`;
  }
  const items = observations.map((o) =>
    `<li class="obs obs-${esc(o.level)}"><span class="obs-icon">${icon[o.level] || "•"}</span>${esc(o.text)}</li>`
  ).join("");
  return summaryLine + `<ul class="obs-list">${items}</ul>` +
    `<p class="disclaimer">说明：以上为基于历史成交的<strong>客观交易行为复盘</strong>（FIFO 配对计算回合盈亏），仅描述已发生的交易特征，<strong>不构成任何投资建议或买卖推荐</strong>。</p>`;
}

function renderRoundTrips(trips) {
  if (!trips || !trips.length) {
    return `<div class="empty">暂无已平仓回合（可能只有买入未卖出，或建仓早于抓取起点）。</div>`;
  }
  const rows = trips.map((t) => `
    <tr>
      <td class="text">${esc(t.buy_time || "—")}${t.fallback ? ' <span class="tag">兜底成本</span>' : ""}</td>
      <td class="text">${esc(t.sell_time || "—")}</td>
      <td>${fmtNum(t.buy_price)}</td>
      <td>${fmtNum(t.sell_price)}</td>
      <td>${fmtInt(t.qty)}</td>
      <td>${fmtDays(t.hold_days)}</td>
      ${plCell(t.pnl)}
      ${plCell(t.pnl_ratio, "%")}
    </tr>`).join("");
  return `<div class="scroll-table"><table>
    <thead><tr>
      <th class="text">买入日</th><th class="text">卖出日</th><th>买入价</th><th>卖出价</th>
      <th>数量</th><th>持有</th><th>回合盈亏</th><th>盈亏率</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderAnalysisDeals(deals) {
  if (!deals.length) return `<div class="empty">无成交记录</div>`;
  const rows = deals.slice().reverse().map((d) => `
    <tr>
      <td class="text">${esc((d.create_time || "").slice(0, 19))}</td>
      <td class="text">${sideBadge(d.trd_side)}</td>
      <td>${fmtNum(d.price)}</td>
      <td>${fmtInt(d.qty)}</td>
      <td>${fmtNum(Number(d.price) * Number(d.qty))}</td>
      <td class="text">${esc(d.order_id || "")}</td>
    </tr>`).join("");
  return `<div class="scroll-table tall"><table>
    <thead><tr>
      <th class="text">成交时间</th><th class="text">方向</th><th>成交价</th>
      <th>数量</th><th>成交额</th><th class="text">关联订单</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

// 可折叠板块：标题点击切换内容显示/隐藏。
function section(title, contentHtml) {
  return `<div class="section">
    <h3 class="section-title" role="button" tabindex="0" aria-expanded="true">
      <span class="section-caret">▾</span>${esc(title)}
    </h3>
    <div class="section-body">${contentHtml}</div>
  </div>`;
}

function bindSectionToggles(scope) {
  scope.querySelectorAll(".section-title").forEach((h) => {
    const toggle = () => {
      const sec = h.parentElement;
      const collapsed = sec.classList.toggle("collapsed");
      h.setAttribute("aria-expanded", String(!collapsed));
    };
    h.addEventListener("click", toggle);
    // 键盘可达：回车 / 空格切换
    h.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
  });
}

// 通用信息：字段顺序与渲染方式（与后端 _PROFILE_LABELS 字段名一致）。
// cur:true 表示该值以标的本币计价（yfinance 按交易货币返回），
// 展示时把币种（货币字段）拼到标签后，避免误标成美元。
const PROFILE_FIELDS = [
  { key: "公司名" }, { key: "板块" }, { key: "行业" }, { key: "交易所" },
  { key: "市值(百万)", num: true, cur: true }, { key: "流通股本(百万)", num: true },
  { key: "市盈率(TTM)", num: true }, { key: "预期市盈率", num: true },
  { key: "市净率", num: true }, { key: "每股收益(TTM)", num: true, cur: true },
  { key: "股息率%", num: true }, { key: "Beta", num: true },
  { key: "目标均价", num: true, cur: true }, { key: "分析师评级" },
  { key: "货币" }, { key: "官网", link: true },
];

async function loadProfile(code) {
  const wrap = document.getElementById("detail-profile");
  if (!wrap) return;
  try {
    const r = await getJSON(`/api/stock/${encodeURIComponent(code)}/profile`);
    if (!r.profile) {
      wrap.innerHTML = `<div class="empty">暂无通用信息（可能未上市 / yfinance 无资料）</div>`;
      return;
    }
    wrap.innerHTML = renderProfile(r.profile);
  } catch (e) {
    wrap.innerHTML = `<div class="empty">通用信息加载失败：${esc(e.message)}</div>`;
  }
}

function renderProfile(p) {
  const cur = p["货币"] ? String(p["货币"]) : "";   // 标的本币，如 USD / HKD
  const items = PROFILE_FIELDS.map((f) => {
    let v = p[f.key];
    if (v === null || v === undefined || v === "") return "";
    // 本币计价字段：标签后拼币种，避免误标成美元。无币种则保持原样。
    //  「市值(百万)」→「市值(百万HKD)」；「目标均价」→「目标均价(HKD)」
    let label = f.key;
    if (f.cur && cur) {
      label = f.key.includes("(百万)")
        ? f.key.replace("(百万)", `(百万${cur})`)
        : `${f.key}(${cur})`;
    }
    let val;
    if (f.link) {
      const url = esc(String(v));
      val = `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`;
    } else if (f.num) {
      val = fmtNum(v);
    } else {
      val = esc(String(v));
    }
    return `<div class="profile-item">
      <span class="profile-key">${esc(label)}</span>
      <span class="profile-val">${val}</span>
    </div>`;
  }).filter(Boolean).join("");
  if (!items) return `<div class="empty">暂无通用信息</div>`;
  return `<div class="profile-grid">${items}</div>`;
}

// 简单的收盘价折线 SVG。涨段红、跌段绿（相对前一日收盘）。
function renderChart(quotes) {
  if (!quotes || quotes.length < 2) {
    return `<div class="empty">行情数据不足，无法绘图</div>`;
  }
  const W = Math.max(720, quotes.length * 6);
  const H = 240, pad = 36;
  const closes = quotes.map((q) => Number(q.close)).filter((x) => isFinite(x));
  const min = Math.min(...closes), max = Math.max(...closes);
  const range = max - min || 1;
  const x = (i) => pad + (i * (W - 2 * pad)) / (quotes.length - 1);
  const y = (v) => H - pad - ((v - min) / range) * (H - 2 * pad);

  let segs = "";
  for (let i = 1; i < quotes.length; i++) {
    const prev = Number(quotes[i - 1].close);
    const cur = Number(quotes[i].close);
    if (!isFinite(prev) || !isFinite(cur)) continue;
    const cls = cur > prev ? "up" : cur < prev ? "down" : "flat";
    const color = cls === "up" ? "var(--up)" : cls === "down" ? "var(--down)" : "var(--flat)";
    segs += `<line x1="${x(i - 1).toFixed(1)}" y1="${y(prev).toFixed(1)}" x2="${x(i).toFixed(1)}" y2="${y(cur).toFixed(1)}" stroke="${color}" stroke-width="1.6"/>`;
  }
  // 轴标签
  const first = quotes[0].date, last = quotes[quotes.length - 1].date;
  return `
    <div class="chart-wrap">
      <svg class="chart" width="${W}" height="${H}">
        <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="var(--border)"/>
        <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H - pad}" stroke="var(--border)"/>
        <text x="${pad}" y="${pad - 12}" fill="var(--muted)" font-size="11">高 ${fmtNum(max)}</text>
        <text x="${pad}" y="${H - pad + 16}" fill="var(--muted)" font-size="11">低 ${fmtNum(min)} · ${first}</text>
        <text x="${W - pad}" y="${H - pad + 16}" fill="var(--muted)" font-size="11" text-anchor="end">${last}</text>
        ${segs}
      </svg>
    </div>`;
}

function renderQuotesTable(quotes) {
  if (!quotes.length) return `<div class="empty">无日线数据</div>`;
  const rows = quotes.slice().reverse().map((q, idx, arr) => {
    // 涨跌相对前一交易日（注意已 reverse）
    const prev = arr[idx + 1];
    const chg = prev ? Number(q.close) - Number(prev.close) : null;
    const chgPct = prev && Number(prev.close) ? (chg / Number(prev.close)) * 100 : null;
    return `<tr>
      <td class="text">${esc(q.date)}</td>
      <td>${fmtNum(q.open)}</td><td>${fmtNum(q.high)}</td>
      <td>${fmtNum(q.low)}</td><td>${fmtNum(q.close)}</td>
      <td>${fmtNum(q.adj_close)}</td>
      ${plCell(chg)}${plCell(chgPct, "%")}
      <td>${fmtInt(q.volume)}</td>
    </tr>`;
  }).join("");
  return `<div class="scroll-table"><table>
    <thead><tr>
      <th class="text">日期</th><th>开</th><th>高</th><th>低</th><th>收</th>
      <th>复权收</th><th>涨跌</th><th>涨跌幅</th><th>成交量</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderDetailOrders(orders) {
  if (!orders.length) return `<div class="empty">无订单</div>`;
  const rows = orders.map((o) => `<tr>
    <td class="text">${sideBadge(o.trd_side)}</td>
    <td class="text">${esc(o.order_status || "")}</td>
    <td>${fmtNum(o.price)}</td><td>${fmtInt(o.qty)}</td>
    <td>${fmtInt(o.dealt_qty)}</td><td>${fmtNum(o.dealt_avg_price)}</td>
    <td class="text">${esc(o.create_time || "")}</td>
  </tr>`).join("");
  return `<div class="scroll-table"><table>
    <thead><tr><th class="text">方向</th><th class="text">状态</th><th>价格</th>
    <th>委托量</th><th>成交量</th><th>成交均价</th><th class="text">时间</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function renderDetailDeals(deals) {
  if (!deals.length) return `<div class="empty">无成交</div>`;
  const rows = deals.map((d) => `<tr>
    <td class="text">${sideBadge(d.trd_side)}</td>
    <td>${fmtNum(d.price)}</td><td>${fmtInt(d.qty)}</td>
    <td class="text">${esc(d.create_time || "")}</td>
    <td class="text">${esc(d.order_id || "")}</td>
  </tr>`).join("");
  return `<div class="scroll-table"><table>
    <thead><tr><th class="text">方向</th><th>成交价</th><th>成交量</th>
    <th class="text">时间</th><th class="text">关联订单</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

// 初始化
loadPositions();
