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
    document.getElementById("panel-trend").style.display = tab === "trend" ? "" : "none";
    document.getElementById("panel-fx").style.display = tab === "fx" ? "" : "none";
    if (tab !== "fx") destroyFxChart();     // 离开汇率 Tab 释放图表
    if (tab !== "trend") destroyTrendCharts();  // 离开趋势 Tab 释放图表
    if (tab === "trades") loadTrades();
    if (tab === "pnl") loadPnl();
    if (tab === "trend") loadTrend();
    if (tab === "fx") loadFx();
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
  orders: { raw: [], market: "", year: "" },
  deals: { raw: [], market: "", year: "" },
  pnl: { raw: [], market: "", sort: { key: null, dir: 0 } },
  finance: { year: "", built: false },
  trend: { raw: [], days: 30 },
  funds: { latest: null, history: [] },
  fx: { raw: [], pair: "USDCNY" },
};

function byMarket(rows, market) {
  return market ? rows.filter((r) => r.market === market) : rows;
}

// 按年份筛选：year 为 "" 时不过滤；否则按 create_time 前 4 位（YYYY-...）匹配。
function byYear(rows, year) {
  return year ? rows.filter((r) => String(r.create_time || "").slice(0, 4) === year) : rows;
}

// ---------- 市场筛选 ----------
document.querySelectorAll('.filter:not([data-filter="year"]):not([data-filter="trend-range"])').forEach((f) => {
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
  // 账户资金：独立加载，失败不影响持仓表（OpenD 未开时可能为空）
  loadAccountFunds();
}

// 账户资金：拉取最新快照 + 历史序列，供组合概览与资产趋势消费。
async function loadAccountFunds() {
  try {
    const data = await getJSON("/api/account-funds");
    state.funds.latest = data.latest || null;
    state.funds.history = data.history || [];
  } catch (e) {
    state.funds.latest = null;
    state.funds.history = [];
  }
  // 资金到手后重渲染组合概览（追加账户总览卡）
  if (state.positions.raw.length) renderPortfolioOverview(state.positions.raw);
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

  // 组合概览始终基于整个快照（不受市场筛选影响）
  renderPortfolioOverview(st.raw);

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

// 组合概览：用最新快照按币种（HKD/USD）分组汇总。零新数据。
// 口径：不同币种不可相加（HK→HKD、US→USD 各成一组）；占比用支数（货币中性）。
// 浮盈率 = 组内浮盈额合计 / 组内成本额合计（成本额 = 市值 - 浮盈，回避 cost_price≤0 的富途超卖噪声）。
function renderPortfolioOverview(rows) {
  const host = document.getElementById("portfolio-overview");
  if (!host) return;
  if (!rows || !rows.length) { host.innerHTML = ""; return; }

  // 市场 → 币种（与全站口径一致）
  const CCY = { HK: "HKD", US: "USD" };
  const NAME = { HK: "港股", US: "美股" };

  // 按市场分组（每个市场即单一币种）
  const groups = {};
  rows.forEach((p) => {
    const mkt = p.market;
    if (!CCY[mkt]) return;
    const g = groups[mkt] || (groups[mkt] = {
      market: mkt, currency: CCY[mkt],
      mv: 0, pl: 0, count: 0, win: 0, loss: 0,
    });
    g.mv += Number(p.market_val) || 0;
    g.pl += Number(p.pl_val) || 0;
    g.count += 1;
    const pl = Number(p.pl_val) || 0;
    if (pl > 0) g.win += 1; else if (pl < 0) g.loss += 1;
  });

  const list = ["US", "HK"].filter((m) => groups[m]).map((m) => groups[m]);
  if (!list.length) { host.innerHTML = ""; return; }

  const totalCount = list.reduce((s, g) => s + g.count, 0);

  const cards = list.map((g) => {
    const cost = g.mv - g.pl;                 // 成本额（回避 cost_price≤0 噪声）
    const plRatio = cost > 0 ? (g.pl / cost) * 100 : null;
    const share = totalCount > 0 ? (g.count / totalCount) * 100 : 0;
    return `
      <div class="pf-card">
        <div class="pf-card-head">
          <span class="pf-mkt">${NAME[g.market]}</span>
          <span class="pf-ccy">${esc(g.currency)}</span>
          <span class="pf-share">${g.count} 支 · 占 ${share.toFixed(0)}%</span>
        </div>
        <div class="pf-mv">${fmtNum(g.mv)}<span class="pf-mv-label">总市值</span></div>
        <div class="pf-rows">
          <div class="pf-row"><span>浮动盈亏</span><span class="${plClass(g.pl)}">${fmtSigned(g.pl.toFixed(2))}</span></div>
          <div class="pf-row"><span>浮盈率</span><span class="${plClass(g.pl)}">${plRatio === null ? "—" : fmtSigned(plRatio.toFixed(2), "%")}</span></div>
          <div class="pf-row"><span>盈利 / 亏损 支数</span><span><span class="up">${g.win}</span> / <span class="down">${g.loss}</span></span></div>
        </div>
      </div>`;
  }).join("");

  // 账户总览卡：来自 account_funds（HK+US 综合账户，HKD 记账）。仅在有数据时展示。
  const acctCard = renderAccountCard();

  // 说明话术按语义分行（<br>），避免一长句拥挤难读。
  const notes = [
    `组合概览基于最新快照（${esc(state.positions.snapshot || "")}）。`,
    acctCard ? "账户总览为 HK+US 综合账户，按 HKD 记账。" : "",
    "其余各市场不同币种不可相加，港股按 HKD、美股按 USD 分别汇总。",
    "占比按持仓支数（货币中性）；浮盈率 = 浮盈额 / 成本额。",
  ].filter(Boolean).join("<br>");

  host.innerHTML = `
    ${acctCard}
    <div class="pf-grid">${cards}</div>
    <div class="disclaimer">${notes}</div>`;
}

// 账户总览卡：真实账户净资产 / 现金 / 仓位 / 购买力（区别于按币种拆的持仓市值卡）。
function renderAccountCard() {
  const f = state.funds.latest;
  if (!f || f.total_assets == null) return "";
  const ccy = f.report_currency || "HKD";
  const ta = Number(f.total_assets);
  const mv = Number(f.market_val) || 0;
  const cash = Number(f.cash) || 0;
  const posRatio = ta > 0 ? (mv / ta) * 100 : null;   // 仓位 = 持仓市值 / 总资产
  const power = f.power == null ? null : Number(f.power);
  const stale = f.snapshot_date && f.snapshot_date !== state.positions.snapshot;
  return `
    <div class="pf-account">
      <div class="pf-account-head">
        <span class="pf-acct-title">账户总览</span>
        <span class="pf-ccy">${esc(ccy)} 记账</span>
        ${stale ? `<span class="pf-acct-stale">资金快照 ${esc(f.snapshot_date)}</span>` : ""}
      </div>
      <div class="pf-account-body">
        <div class="pf-acct-metric">
          <div class="pf-acct-val">${fmtNum(ta)}</div>
          <div class="pf-acct-label">总资产</div>
        </div>
        <div class="pf-acct-metric">
          <div class="pf-acct-val">${fmtNum(mv)}</div>
          <div class="pf-acct-label">持仓市值</div>
        </div>
        <div class="pf-acct-metric">
          <div class="pf-acct-val">${fmtNum(cash)}</div>
          <div class="pf-acct-label">现金</div>
        </div>
        <div class="pf-acct-metric">
          <div class="pf-acct-val">${posRatio === null ? "—" : posRatio.toFixed(1) + "%"}</div>
          <div class="pf-acct-label">仓位</div>
        </div>
        <div class="pf-acct-metric">
          <div class="pf-acct-val">${power === null ? "—" : fmtNum(power)}</div>
          <div class="pf-acct-label">最大购买力</div>
        </div>
      </div>
    </div>`;
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
  buildYearFilter();
}

// 时间筛选：最近三年做成子 Tab（chip），更早年份收进下拉框。
// 默认只显示最近一年（当前年）。年份取自 orders + deals 的 create_time。
function buildYearFilter() {
  const filter = document.querySelector('.filter[data-filter="year"]');
  if (!filter) return;
  const tabsWrap = filter.querySelector(".year-tabs");
  const select = filter.querySelector(".year-select");

  // 汇总数据中出现过的全部年份（降序）
  const years = new Set();
  [...state.orders.raw, ...state.deals.raw].forEach((r) => {
    const y = String(r.create_time || "").slice(0, 4);
    if (/^\d{4}$/.test(y)) years.add(y);
  });
  const allYears = [...years].sort().reverse();

  const recent = allYears.slice(0, 3);   // 最近三年 → 子 Tab
  const older = allYears.slice(3);       // 更早 → 下拉框
  const defaultYear = recent[0] || "";   // 默认最近一年

  // 子 Tab（chip）
  tabsWrap.innerHTML = recent
    .map((y) => `<span class="chip" data-year="${y}">${y}</span>`)
    .join("");

  // 下拉框：占位项 + 更早年份；没有更早年份则隐藏
  if (older.length) {
    select.innerHTML =
      `<option value="">更早…</option>` +
      older.map((y) => `<option value="${y}">${y}</option>`).join("");
    select.style.display = "";
  } else {
    select.innerHTML = "";
    select.style.display = "none";
  }

  // 应用默认年份并高亮对应 chip
  state.orders.year = defaultYear;
  state.deals.year = defaultYear;
  setActiveYear(filter, defaultYear);

  // chip 点击（全部 + 最近三年）
  filter.querySelectorAll(".chip[data-year]").forEach((chip) => {
    chip.addEventListener("click", () => {
      applyYear(filter, chip.dataset.year);
      select.value = "";   // 选了 chip 就重置下拉框
    });
  });

  // 下拉框选择更早年份
  select.addEventListener("change", () => {
    if (select.value) applyYear(filter, select.value, /*fromSelect=*/true);
  });

  renderOrders();
  renderDeals();
}

function applyYear(filter, year, fromSelect) {
  state.orders.year = year;
  state.deals.year = year;
  setActiveYear(filter, fromSelect ? null : year);  // 来自下拉时不高亮任何 chip
  renderOrders();
  renderDeals();
}

// 高亮匹配 year 的 chip（year=null 时全部取消高亮，用于下拉选择更早年份）
function setActiveYear(filter, year) {
  filter.querySelectorAll(".chip[data-year]").forEach((c) => {
    c.classList.toggle("active", year !== null && c.dataset.year === year);
  });
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
  const list = byYear(byMarket(state.orders.raw, state.orders.market), state.orders.year);
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
  const list = byYear(byMarket(state.deals.raw, state.deals.market), state.deals.year);
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
  loadFinance();   // 同一面板下的「财务统计」板块（默认当前年）
}

// ---------- 财务统计（年度现金流：收-付，按美股/港股分别汇总）----------
let financeLoaded = false;
async function loadFinance(year) {
  const body = document.getElementById("finance-body");
  // 首次进入用默认年（当前年）；之后由年份筛选传入
  const y = year || state.finance.year || String(new Date().getFullYear());
  state.finance.year = y;
  financeLoaded = true;
  body.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const data = await getJSON(`/api/finance?year=${encodeURIComponent(y)}`);
    if (!state.finance.built) buildFinanceYearFilter(data.available_years || []);
    renderFinance(data);
  } catch (e) {
    financeLoaded = false;
    body.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

// 年份子 Tab：用数据中出现过的全部年份构建，默认高亮当前年。
function buildFinanceYearFilter(years) {
  const filter = document.querySelector('.filter[data-filter="finance"]');
  if (!filter) return;
  const tabsWrap = filter.querySelector(".year-tabs");
  const list = years.length ? years : [String(new Date().getFullYear())];

  tabsWrap.innerHTML = list
    .map((y) => `<span class="chip${y === state.finance.year ? " active" : ""}" data-year="${y}">${y}</span>`)
    .join("");

  tabsWrap.querySelectorAll(".chip[data-year]").forEach((chip) => {
    chip.addEventListener("click", () => {
      tabsWrap.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      loadFinance(chip.dataset.year);
    });
  });
  state.finance.built = true;
}

function renderFinance(data) {
  const body = document.getElementById("finance-body");
  const markets = data.markets || [];
  if (!markets.length) {
    body.innerHTML = `<div class="empty">${esc(data.year)} 年度无成交记录。</div>`;
    return;
  }
  const NAME = { US: "美股", HK: "港股" };
  const cards = markets.map((m) => {
    const net = Number(m.net_cashflow);
    return `
      <div class="finance-card">
        <div class="finance-card-head">
          <span class="finance-mkt">${NAME[m.market] || esc(m.market)}</span>
          <span class="finance-ccy">${esc(m.currency || "")}</span>
        </div>
        <div class="finance-net ${plClass(net)}">${fmtSigned(net.toFixed(2))}</div>
        <div class="finance-net-label">净现金流（卖出额 − 买入额）</div>
        <div class="finance-rows">
          <div class="finance-row"><span>卖出额</span><span class="up">${fmtNum(m.sell_amount)}</span></div>
          <div class="finance-row"><span>买入额</span><span class="down">${fmtNum(m.buy_amount)}</span></div>
          <div class="finance-row"><span>卖出 / 买入笔数</span><span>${fmtInt(m.sell_count)} / ${fmtInt(m.buy_count)}</span></div>
        </div>
      </div>`;
  }).join("");

  body.innerHTML = `
    <div class="finance-grid">${cards}</div>
    <div class="disclaimer">口径：年度现金流（当年卖出总额 − 当年买入总额），仅统计该年度内的成交，不跨年配对成本。金额为标的本币（美股 USD / 港股 HKD），两市场不可相加。若当年只建仓未卖出，净现金流为负属正常支出，非真实亏损。</div>`;
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

// ---------- 资产趋势（历史快照聚合，按币种分市值/浮盈两图）----------
let trendLoaded = false;
let _trendCharts = [];   // 持有已挂载的图表实例，切 Tab 时统一销毁

// 时间窗切换（30/90/360/全部）：改窗后重渲染（数据已全量缓存，纯前端过滤）
document.querySelectorAll('.filter[data-filter="trend-range"] .chip').forEach((chip) => {
  chip.addEventListener("click", () => {
    if (chip.classList.contains("disabled")) return;   // 数据不足的档不可点
    const f = chip.closest(".filter");
    f.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    state.trend.days = Number(chip.dataset.days) || 0;
    if (state.trend.raw.length) renderTrend();
  });
});

// 数据跨度不足的档位置灰禁用，并把默认选中落到「能填满的最大档」。
// 数据每天 +1，档位会随之自动解锁。返回选中的 days。
function syncTrendRangeChips() {
  const chips = [...document.querySelectorAll('.filter[data-filter="trend-range"] .chip')];
  if (!chips.length) return state.trend.days;
  const dates = [...new Set(state.trend.raw.map((r) => r.date))].sort();
  // 数据实际跨度（自然日，含端点）
  let span = 0;
  if (dates.length >= 2) {
    span = Math.round(
      (new Date(dates[dates.length - 1]) - new Date(dates[0])) / 86400000
    ) + 1;
  }

  // 某档是否可用：days=0（全部）恒可用；否则要求数据跨度 ≥ 该档
  const usable = (days) => days === 0 || span >= days;

  chips.forEach((c) => {
    const d = Number(c.dataset.days) || 0;
    const ok = usable(d);
    c.classList.toggle("disabled", !ok);
    c.title = ok ? "" : `数据跨度仅 ${span} 天，不足 ${d} 天`;
  });

  // 若当前选中档已不可用，落到「可用的最大档」（优先大区间，最后是全部）
  const order = [360, 90, 30, 0];   // 从大到小；0=全部兜底
  if (!usable(state.trend.days)) {
    state.trend.days = order.find((d) => usable(d));
  }
  // 高亮当前选中档
  chips.forEach((c) => {
    c.classList.toggle("active", (Number(c.dataset.days) || 0) === state.trend.days);
  });
  return state.trend.days;
}

async function loadTrend() {
  const info = document.getElementById("trend-info");
  if (trendLoaded) { mountTrendCharts(); return; }   // 重入：重建图表
  trendLoaded = true;
  info.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const data = await getJSON("/api/asset-trend");
    state.trend.raw = data.rows || [];
    syncTrendRangeChips();   // 依据数据跨度置灰档位并定默认档
    renderTrend();
  } catch (e) {
    trendLoaded = false;   // 失败允许重试
    info.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

// 按时间窗过滤原始行：days=0 表示全部；否则保留最新快照日往前 days 个自然日内的行。
function trendRows() {
  const rows = state.trend.raw;
  const days = state.trend.days;
  if (!days || !rows.length) return rows;
  const dates = rows.map((r) => r.date).sort();
  const last = dates[dates.length - 1];
  // 截止日往前 days 天（含端点）；字符串日期直接比较即可
  const cutoff = new Date(last + "T00:00:00");
  cutoff.setDate(cutoff.getDate() - (days - 1));
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  return rows.filter((r) => r.date >= cutoffStr);
}

// 把过滤后的行按市场分组成时间序列：{US:[{date,mv,pl}...], HK:[...]}
function trendSeries() {
  const byMkt = {};
  trendRows().forEach((r) => {
    (byMkt[r.market] || (byMkt[r.market] = [])).push({
      date: r.date,
      mv: Number(r.market_val) || 0,
      pl: Number(r.pl_val) || 0,
    });
  });
  return byMkt;
}

const TREND_MKT = { US: { name: "美股", ccy: "USD" }, HK: { name: "港股", ccy: "HKD" } };

function renderTrend() {
  const info = document.getElementById("trend-info");
  const disc = document.getElementById("trend-disclaimer");
  const rows = trendRows();
  const dates = [...new Set(rows.map((r) => r.date))].sort();
  if (dates.length < 2) {
    const hint = state.trend.raw.length && state.trend.days
      ? `当前时间窗（${state.trend.days} 天）内快照不足 2 天，试试更长区间或「全部」。`
      : `历史快照不足（需 ≥ 2 天）。每次 update 会新增一天，积累后即可看趋势。`;
    info.innerHTML = `<div class="empty">${hint}</div>`;
    disc.textContent = "";
    destroyTrendCharts();
    return;
  }

  const byMkt = trendSeries();
  // 每市场：首末市值/浮盈、区间变化
  const cards = ["US", "HK"].filter((m) => byMkt[m]).map((m) => {
    const s = byMkt[m], meta = TREND_MKT[m];
    const first = s[0], last = s[s.length - 1];
    const mvChg = last.mv - first.mv;
    const mvPct = first.mv > 0 ? (mvChg / first.mv) * 100 : null;
    return `
      <div class="trend-card">
        <div class="trend-card-head"><span class="trend-mkt">${meta.name}</span><span class="trend-ccy">${meta.ccy}</span></div>
        <div class="trend-mv">${fmtNum(last.mv)}<span class="trend-mv-label">最新市值</span></div>
        <div class="trend-rows">
          <div class="trend-row"><span>区间市值变化</span><span class="${plClass(mvChg)}">${fmtSigned(mvChg.toFixed(0))}${mvPct === null ? "" : ` (${fmtSigned(mvPct.toFixed(2), "%")})`}</span></div>
          <div class="trend-row"><span>最新浮动盈亏</span><span class="${plClass(last.pl)}">${fmtSigned(last.pl.toFixed(0))}</span></div>
        </div>
      </div>`;
  }).join("");

  const acctCard = renderNetAssetCard(dates);
  info.innerHTML = `<div class="trend-grid">${acctCard}${cards}</div>`;
  const windowLabel = state.trend.days ? `近 ${state.trend.days} 天` : "全部区间";
  disc.textContent =
    `${windowLabel}：${dates.length} 个快照（${dates[0]} ~ ${dates[dates.length - 1]}）。` +
    `不同币种不可相加，美股（USD）与港股（HKD）各一条线、同图双 Y 轴。` +
    `周末/休市日快照沿用前值，趋势线呈平台期属正常。`;

  renderTrendTable();
  mountTrendCharts();
}

// 区间市值变化表：每个快照日一行，市值 + 相对上一快照日的环比%（按市场分列）。
// 最新日在上（倒序），便于一眼看近期变化。
function renderTrendTable() {
  const wrap = document.getElementById("trend-table");
  if (!wrap) return;
  const byMkt = trendSeries();
  const dates = [...new Set(trendRows().map((r) => r.date))].sort();

  // 市场 → {date: {mv, pct}}；pct 相对该市场上一快照日
  const pctByMkt = {};
  ["US", "HK"].forEach((m) => {
    const s = byMkt[m];
    if (!s) return;
    const map = {};
    s.forEach((row, i) => {
      const prev = i > 0 ? s[i - 1].mv : null;
      map[row.date] = {
        mv: row.mv,
        pct: prev && prev > 0 ? ((row.mv - prev) / prev) * 100 : null,
      };
    });
    pctByMkt[m] = map;
  });

  const cols = ["US", "HK"].filter((m) => byMkt[m]);
  const ths = cols.map((m) =>
    `<th>${TREND_MKT[m].name}市值(${TREND_MKT[m].ccy})</th><th>环比</th>`
  ).join("");

  const rows = dates.slice().reverse().map((date) => {
    const cells = cols.map((m) => {
      const d = pctByMkt[m][date];
      if (!d) return `<td>—</td><td>—</td>`;
      // |pct| < 0.005 四舍五入后为 0，归一成 0.00% 避免出现 -0.00%
      const pct = d.pct === null ? null : (Math.abs(d.pct) < 0.005 ? 0 : d.pct);
      const pctTxt = pct === null ? "—" : fmtSigned(pct.toFixed(2), "%");
      return `<td>${fmtNum(d.mv, 0)}</td><td class="${plClass(pct)}">${pctTxt}</td>`;
    }).join("");
    return `<tr><td class="text">${esc(date)}</td>${cells}</tr>`;
  }).join("");

  wrap.innerHTML = `<div class="scroll-table"><table>
    <thead><tr><th class="text">日期</th>${ths}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

// 账户净资产趋势卡：来自 account_funds 历史序列（HKD 综合记账），
// 按当前趋势时间窗过滤后取首末，展示最新净资产 + 区间变化。有数据才显示。
function renderNetAssetCard(windowDates) {
  const hist = state.funds.history || [];
  if (hist.length < 1 || !windowDates.length) return "";
  // 与趋势图同窗（起点对齐）：取时间窗起点当日及之后的资金快照。
  // 只用下界、不设上界——资金快照仅向前积累，最新一条（可能晚于持仓快照日）
  // 始终是最相关的，避免因持仓/资金快照日不同步而漏显。
  const lo = windowDates[0];
  const inWin = hist.filter((r) => r.snapshot_date >= lo && r.total_assets != null);
  if (!inWin.length) return "";
  const ccy = inWin[inWin.length - 1].report_currency || "HKD";
  const first = Number(inWin[0].total_assets);
  const last = Number(inWin[inWin.length - 1].total_assets);
  const chg = last - first;
  const pct = first > 0 ? (chg / first) * 100 : null;
  const single = inWin.length < 2;   // 窗内只有一天：无从算区间变化
  return `
    <div class="trend-card trend-card-acct">
      <div class="trend-card-head"><span class="trend-mkt">账户净资产</span><span class="trend-ccy">${esc(ccy)}</span></div>
      <div class="trend-mv">${fmtNum(last)}<span class="trend-mv-label">最新总资产</span></div>
      <div class="trend-rows">
        <div class="trend-row"><span>区间净资产变化</span><span class="${single ? "" : plClass(chg)}">${single ? "—" : fmtSigned(chg.toFixed(0)) + (pct === null ? "" : ` (${fmtSigned(pct.toFixed(2), "%")})`)}</span></div>
        <div class="trend-row"><span>快照天数</span><span>${inWin.length} 天</span></div>
      </div>
    </div>`;
}

function destroyTrendCharts() {
  _trendCharts.forEach((ch) => { try { ch.remove(); } catch (e) {} });
  _trendCharts = [];
}

// 在一个容器里画两市场的时序：US 与 HK 各一条线，各自独立 Y 轴（量级差大）。
// pick(row) 从 {mv,pl} 取要画的值。
function mountOneTrendChart(hostId, pick) {
  const host = document.getElementById(hostId);
  if (!host || typeof LightweightCharts === "undefined") return;
  const byMkt = trendSeries();
  const c = chartColors();

  const chart = LightweightCharts.createChart(host, {
    width: host.clientWidth || 720,
    height: 300,
    layout: { background: { color: "transparent" }, textColor: c.muted },
    grid: { vertLines: { color: c.border }, horzLines: { color: c.border } },
    rightPriceScale: { borderColor: c.border },       // US（USD）
    leftPriceScale: { borderColor: c.border, visible: true },  // HK（HKD）
    timeScale: { borderColor: c.border, timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  // US 走右轴（accent 蓝），HK 走左轴（muted 灰），避免与红涨绿跌语义混淆
  const cfg = [
    { m: "US", scaleId: "right", color: c.accent },
    { m: "HK", scaleId: "left", color: c.muted },
  ];
  cfg.forEach(({ m, scaleId, color }) => {
    if (!byMkt[m]) return;
    const line = chart.addLineSeries({
      color, lineWidth: 2, priceScaleId: scaleId,
      priceLineVisible: false, lastValueVisible: true,
      title: TREND_MKT[m].name,
    });
    line.setData(byMkt[m].map((r) => ({ time: r.date, value: pick(r) })));
  });
  chart.timeScale().fitContent();

  const ro = new ResizeObserver(() => {
    if (_trendCharts.includes(chart)) chart.applyOptions({ width: host.clientWidth });
  });
  ro.observe(host);
  _trendCharts.push(chart);
}

function mountTrendCharts() {
  destroyTrendCharts();
  if (trendRows().length < 2) return;
  mountOneTrendChart("trend-mv-chart", (r) => r.mv);
  mountOneTrendChart("trend-pl-chart", (r) => r.pl);
}

// ---------- 美元汇率（USD/CNY）----------
let fxLoaded = false;
let _fxChartHandle = null;

async function loadFx() {
  const info = document.getElementById("fx-info");
  const host = document.getElementById("fx-chart");
  if (fxLoaded) { mountFxChart(); return; }   // 重入：重建图表
  fxLoaded = true;
  info.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const data = await getJSON(`/api/fx?pair=${encodeURIComponent(state.fx.pair)}`);
    state.fx.raw = data.rows || [];
    renderFx();
  } catch (e) {
    fxLoaded = false;  // 失败允许重试
    info.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    host.innerHTML = "";
  }
}

function renderFx() {
  const info = document.getElementById("fx-info");
  const host = document.getElementById("fx-chart");
  // 只取有收盘价的交易日（当天未收盘的行 close 可能为空）
  const rows = state.fx.raw.filter((r) => r.close !== null && r.close !== "" && isFinite(Number(r.close)));
  if (rows.length < 2) {
    info.innerHTML = `<div class="empty">暂无汇率数据，请先运行 update.sh。</div>`;
    host.innerHTML = "";
    return;
  }

  const first = rows[0], last = rows[rows.length - 1];
  const closes = rows.map((r) => Number(r.close));
  const min = Math.min(...closes), max = Math.max(...closes);
  const chg = Number(last.close) - Number(first.close);
  const chgPct = (chg / Number(first.close)) * 100;

  // 基本信息卡（中性配色，不套红涨绿跌：汇率涨跌语义中性）
  info.innerHTML = `
    <div class="fx-head">
      <div class="fx-pair">美元 / 人民币 · USDCNY</div>
      <div class="fx-latest">${fmtNum(last.close, 4)}
        <span class="fx-sub">最新（${esc(last.date)}）</span></div>
    </div>
    <div class="metric-flex">
      <div class="metric"><div class="metric-label">区间最高</div><div class="metric-value">${fmtNum(max, 4)}</div></div>
      <div class="metric"><div class="metric-label">区间最低</div><div class="metric-value">${fmtNum(min, 4)}</div></div>
      <div class="metric"><div class="metric-label">区间涨跌</div><div class="metric-value">${fmtSigned(chg.toFixed(4))} (${fmtSigned(chgPct.toFixed(2), "%")})</div></div>
      <div class="metric"><div class="metric-label">数据区间</div><div class="metric-value fx-range">${esc(first.date)} ~ ${esc(last.date)}</div></div>
      <div class="metric"><div class="metric-label">交易日数</div><div class="metric-value">${fmtInt(rows.length)}</div></div>
    </div>
    <div class="disclaimer">汇率来源 yfinance（CNY=X）；close = 1 美元对应的人民币。涨跌仅为客观变动，不代表方向判断。</div>`;

  mountFxChart();
}

function destroyFxChart() {
  if (_fxChartHandle) {
    try { _fxChartHandle.remove(); } catch (e) {}
    _fxChartHandle = null;
  }
}

// 折线趋势图（中性主题色）。USDCNY 单一汇率看趋势用折线优于蜡烛。
function mountFxChart() {
  destroyFxChart();
  const host = document.getElementById("fx-chart");
  const rows = state.fx.raw.filter((r) => r.close !== null && r.close !== "" && isFinite(Number(r.close)));
  if (!host || rows.length < 2 || typeof LightweightCharts === "undefined") return;

  const c = chartColors();
  const chart = LightweightCharts.createChart(host, {
    width: host.clientWidth || 720,
    height: 320,
    layout: { background: { color: "transparent" }, textColor: c.muted },
    grid: { vertLines: { color: c.border }, horzLines: { color: c.border } },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border, timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  const line = chart.addLineSeries({
    color: c.accent, lineWidth: 2,
    priceLineVisible: false, lastValueVisible: true,
  });
  line.setData(rows.map((r) => ({ time: r.date, value: Number(r.close) })));
  chart.timeScale().fitContent();

  const ro = new ResizeObserver(() => {
    if (_fxChartHandle === chart) chart.applyOptions({ width: host.clientWidth });
  });
  ro.observe(host);
  _fxChartHandle = chart;
}

// ---------- 个股详情下钻 ----------
const overlay = document.getElementById("overlay");
document.getElementById("detail-close").addEventListener("click", () => { overlay.classList.remove("open"); destroyChart(); });
overlay.addEventListener("click", (e) => { if (e.target === overlay) { overlay.classList.remove("open"); destroyChart(); } });

// 数据新鲜度提示：把最新日线日期与滞后天数亮出来。
// 行情为只读库、天然慢一拍（当天 bar 次日入），滞后 > 3 天则提示可能需 update。
function dataFreshnessBanner(quotes) {
  if (!quotes || !quotes.length) return "";
  const last = quotes[quotes.length - 1].date;
  const today = new Date();
  const lag = Math.round((today - new Date(last + "T00:00:00")) / 86400000);
  const stale = lag > 3;
  const lagTxt = lag <= 0 ? "今日" : `${lag} 天前`;
  return `<div class="freshness${stale ? " stale" : ""}">
    <span class="freshness-dot"></span>
    最新数据时间：<b>${esc(last)}</b>（${lagTxt}）${stale ? " · 数据可能滞后，建议运行 update.sh" : ""}</div>`;
}

async function openStock(code) {
  destroyChart();   // 重开前销毁上一个图表实例
  overlay.classList.add("open");
  document.getElementById("detail-title").textContent = code;
  const body = document.getElementById("detail-body");
  body.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}`);
    document.getElementById("detail-title").textContent =
      d.name ? `${code} · ${d.name}` : code;
    body.innerHTML =
      dataFreshnessBanner(d.quotes) +
      section("通用信息", `<div id="detail-profile"><div class="empty">加载通用信息中…</div></div>`) +
      section("价格走势（K线）", renderChart(d.quotes)) +
      section("主力资金流向（近 60 日）", renderCapitalFlow()) +
      section(`历史日线（${d.quotes.length} 条）`, renderQuotesTable(d.quotes)) +
      section(`我的订单（${d.orders.length} 条）`, renderDetailOrders(d.orders)) +
      section(`我的成交（${d.deals.length} 条）`, renderDetailDeals(d.deals));
    bindSectionToggles(body);
    mountChart();          // 占位容器已入 DOM，挂载蜡烛图 + 成交量
    loadProfile(code);     // 通用信息走 yfinance 实时接口，异步填充
    loadCapitalFlow(code); // 资金流向读库，异步填充后挂载柱图
  } catch (e) {
    body.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

// ---------- 交易复盘浮窗（与个股详情共用 overlay）----------
async function openAnalysis(code) {
  destroyChart();   // 复盘浮窗不含图表，复用 overlay 前先销毁残留实例
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
  // 盘面增量字段（富途快照）：换手率/振幅为百分比；52 周高低为本币价格。
  { key: "换手率%", num: true }, { key: "振幅%", num: true },
  { key: "52周最高", num: true, cur: true }, { key: "52周最低", num: true, cur: true },
];

// ---------- 主力资金流向（富途日频）----------
// 资金流向金额动辄上亿，直接展示会淹没在零里 → 压成 亿/万 单位。
// 返回带符号字符串（正=净流入）。
function fmtFlow(v) {
  const n = Number(v);
  if (v === null || v === undefined || v === "" || !isFinite(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)} 亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(1)} 万`;
  return `${sign}${abs.toFixed(0)}`;
}

let _flowHandle = null;   // 资金流向图实例
let _flowData = null;     // 待挂载数据

function renderCapitalFlow() {
  // 与 K 线同样的两段式：先出占位容器，数据到位后由 mountFlowChart 挂载。
  return `<div id="flow-host-wrap"><div class="empty">加载资金流向中…</div></div>`;
}

function destroyFlowChart() {
  if (_flowHandle) {
    try { _flowHandle.remove(); } catch (e) {}
    _flowHandle = null;
  }
  _flowData = null;
}

async function loadCapitalFlow(code) {
  const wrap = document.getElementById("flow-host-wrap");
  if (!wrap) return;
  try {
    const r = await getJSON(`/api/stock/${encodeURIComponent(code)}/capital-flow?days=60`);
    const rows = (r.rows || []).filter((x) => x.main_in_flow !== null && x.main_in_flow !== undefined);
    if (!rows.length) {
      wrap.innerHTML = `<div class="empty">暂无资金流向数据（需运行 update.sh 抓取）</div>`;
      return;
    }
    // 汇总：近 N 日主力净流入合计 + 流入/流出天数，给柱图一个结论性抬头
    let sum = 0, inDays = 0, outDays = 0;
    for (const x of rows) {
      const v = Number(x.main_in_flow);
      sum += v;
      if (v > 0) inDays++; else if (v < 0) outDays++;
    }
    wrap.innerHTML = `
      <div class="flow-summary">
        近 ${rows.length} 日主力净流入合计
        <b class="${plClass(sum)}">${fmtFlow(sum)}</b>
        <span class="flow-sub">（流入 ${inDays} 天 / 流出 ${outDays} 天，标的本币）</span>
      </div>
      <div class="chart-host" id="flow-host"></div>`;
    _flowData = rows;
    mountFlowChart();
  } catch (e) {
    wrap.innerHTML = `<div class="empty">资金流向加载失败：${esc(e.message)}</div>`;
  }
}

// 主力净流入柱状图：红=净流入 / 绿=净流出（中国习惯，与 K 线一致）。
function mountFlowChart() {
  const host = document.getElementById("flow-host");
  if (!host || !_flowData || !_flowData.length) return;
  if (typeof LightweightCharts === "undefined") {
    host.innerHTML = `<div class="empty">图表库未加载</div>`;
    return;
  }
  const c = chartColors();
  const chart = LightweightCharts.createChart(host, {
    width: host.clientWidth || 720,
    height: 220,
    layout: { background: { color: "transparent" }, textColor: c.muted },
    grid: { vertLines: { color: c.border }, horzLines: { color: c.border } },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border, timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  const series = chart.addHistogramSeries({
    priceFormat: { type: "volume" },   // 紧凑数值轴，避免亿级数字撑开刻度
    base: 0,                            // 以 0 为基线，正上负下
  });
  series.setData(_flowData.map((r) => {
    const v = Number(r.main_in_flow);
    return { time: r.date, value: v, color: v >= 0 ? c.up : c.down };
  }));
  chart.timeScale().fitContent();

  const ro = new ResizeObserver(() => {
    if (_flowHandle === chart) chart.applyOptions({ width: host.clientWidth });
  });
  ro.observe(host);

  _flowHandle = chart;
}

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

// ---------- 价格走势：蜡烛图 + 成交量（Lightweight-Charts）----------
// 该库需真实 DOM 容器 + 创建后注入数据，故 renderChart 只产出占位容器，
// 数据暂存到 _chartData，待 DOM 插入后由 mountChart 挂载。
let _chartHandle = null;   // 当前图表实例（浮窗关闭时销毁）
let _chartData = null;     // 待挂载的 quotes

function renderChart(quotes) {
  _chartData = quotes;
  if (!quotes || quotes.length < 2) {
    return `<div class="empty">行情数据不足，无法绘图</div>`;
  }
  return `<div class="chart-host" id="chart-host"></div>`;
}

// 读取当前主题下的 CSS 变量颜色（红涨绿跌、深浅主题一致）。
function chartColors() {
  const cs = getComputedStyle(document.body);
  const v = (name, fallback) => (cs.getPropertyValue(name).trim() || fallback);
  return {
    up: v("--up", "#e23d3d"),
    down: v("--down", "#1ca362"),
    border: v("--border", "#ddd"),
    muted: v("--muted", "#888"),
    text: v("--text", "#222"),
    bg: v("--panel", "#fff"),
    accent: v("--accent", "#3b82f6"),
  };
}

// 销毁当前图表（浮窗关闭/重开时调用），避免内存泄漏与重复挂载。
// 资金流向图与 K 线同生共死（同一浮窗），一并销毁，免得每处关闭都要记着调两个。
function destroyChart() {
  if (_chartHandle) {
    try { _chartHandle.remove(); } catch (e) {}
    _chartHandle = null;
  }
  _chartData = null;
  destroyFlowChart();
}

// 在占位容器已插入 DOM 后挂载蜡烛图 + 成交量副图。
function mountChart() {
  const host = document.getElementById("chart-host");
  if (!host || !_chartData || _chartData.length < 2) return;
  if (typeof LightweightCharts === "undefined") {
    host.innerHTML = `<div class="empty">图表库未加载</div>`;
    return;
  }
  const c = chartColors();
  const chart = LightweightCharts.createChart(host, {
    width: host.clientWidth || 720,
    height: 320,
    layout: { background: { color: "transparent" }, textColor: c.muted },
    grid: {
      vertLines: { color: c.border },
      horzLines: { color: c.border },
    },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border, timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  // 蜡烛主图：红涨绿跌
  const candle = chart.addCandlestickSeries({
    upColor: c.up, downColor: c.down,
    wickUpColor: c.up, wickDownColor: c.down,
    borderUpColor: c.up, borderDownColor: c.down,
  });
  const candleData = [];
  const volData = [];
  for (const q of _chartData) {
    const o = Number(q.open), h = Number(q.high), l = Number(q.low), cl = Number(q.close);
    if (![o, h, l, cl].every(isFinite)) continue;
    candleData.push({ time: q.date, open: o, high: h, low: l, close: cl });
    const vol = Number(q.volume);
    if (isFinite(vol)) {
      volData.push({ time: q.date, value: vol, color: cl >= o ? c.up : c.down });
    }
  }
  candle.setData(candleData);

  // 成交量副图：底部 20% 高度的直方图
  const vol = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "vol",
  });
  chart.priceScale("vol").applyOptions({
    scaleMargins: { top: 0.8, bottom: 0 },
  });
  vol.setData(volData);

  chart.timeScale().fitContent();

  // 容器宽度自适应（浮窗大小变化时）
  const ro = new ResizeObserver(() => {
    if (_chartHandle === chart) chart.applyOptions({ width: host.clientWidth });
  });
  ro.observe(host);

  _chartHandle = chart;
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
