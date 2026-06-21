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

// ---------- Tab 切换 ----------
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    const tab = t.dataset.tab;
    document.getElementById("panel-positions").style.display = tab === "positions" ? "" : "none";
    document.getElementById("panel-trades").style.display = tab === "trades" ? "" : "none";
    if (tab === "trades") loadTrades();
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

  // 排序（dir: 1 升 / -1 降 / 0 原始顺序）
  if (st.sort.key && st.sort.dir !== 0) {
    const { key, dir } = st.sort;
    list = list.slice().sort((a, b) => {
      const x = Number(a[key]), y = Number(b[key]);
      const xn = isFinite(x), yn = isFinite(y);
      if (!xn && !yn) return 0;
      if (!xn) return 1;            // 空值恒排末尾
      if (!yn) return -1;
      return (x - y) * dir;
    });
  }

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
  bindSortHeaders(wrap);
}

// 表头点击：倒序 → 正序 → 取消，循环切换
function bindSortHeaders(scope) {
  scope.querySelectorAll("th.sortable[data-sortkey]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sortkey;
      const st = state.positions.sort;
      if (st.key !== key) {
        st.key = key; st.dir = -1;        // 新列：先倒序
      } else if (st.dir === -1) {
        st.dir = 1;                       // 倒序 → 正序
      } else if (st.dir === 1) {
        st.key = null; st.dir = 0;        // 正序 → 取消
      } else {
        st.dir = -1;                      // 取消 → 倒序
      }
      renderPositions();
    });
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
    body.innerHTML = `
      <h3>价格走势（收盘价）</h3>
      ${renderChart(d.quotes)}
      <h3>历史日线（${d.quotes.length} 条）</h3>
      ${renderQuotesTable(d.quotes)}
      <h3>我的订单（${d.orders.length} 条）</h3>
      ${renderDetailOrders(d.orders)}
      <h3>我的成交（${d.deals.length} 条）</h3>
      ${renderDetailDeals(d.deals)}
    `;
  } catch (e) {
    body.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
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
