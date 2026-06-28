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
    document.getElementById("panel-fx").style.display = tab === "fx" ? "" : "none";
    if (tab !== "fx") destroyFxChart();   // 离开汇率 Tab 释放图表
    if (tab === "trades") loadTrades();
    if (tab === "pnl") loadPnl();
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
document.querySelectorAll('.filter:not([data-filter="year"])').forEach((f) => {
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
      section("通用信息", `<div id="detail-profile"><div class="empty">加载通用信息中…</div></div>`) +
      section("价格走势（K线）", renderChart(d.quotes)) +
      section(`历史日线（${d.quotes.length} 条）`, renderQuotesTable(d.quotes)) +
      section(`我的订单（${d.orders.length} 条）`, renderDetailOrders(d.orders)) +
      section(`我的成交（${d.deals.length} 条）`, renderDetailDeals(d.deals));
    bindSectionToggles(body);
    mountChart();        // 占位容器已入 DOM，挂载蜡烛图 + 成交量
    loadProfile(code);   // 通用信息走 yfinance 实时接口，异步填充
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
function destroyChart() {
  if (_chartHandle) {
    try { _chartHandle.remove(); } catch (e) {}
    _chartHandle = null;
  }
  _chartData = null;
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
