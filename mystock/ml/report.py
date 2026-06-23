"""P5 — 每日回测 HTML 报告（自包含单文件，归档到 data/ml/reports/<date>/）。

无外部依赖：纯 Python 生成 HTML + 内联 SVG 净值曲线（无 JS 库）。
配色沿用项目"红涨绿跌"（CLAUDE.md 约定）。绝不碰 web / 生产库。
运行：python -m mystock.ml.report  → 生成当日报告 + 更新 latest.html
"""
from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from . import config as mlcfg
from . import data as mldata
from .backtest import BTConfig, run_backtest
from .predictor import predict_next_day

# 红涨绿跌
C_UP = "#d33"      # 涨/正
C_DOWN = "#127a3d" # 跌/负
C_GRID = "#ddd"
COLORS = {  # 各曲线配色
    "bandit": "#d33", "rule": "#e8912a", "human": "#2a6fd8", "buy_hold": "#888",
}
LABELS = {"bandit": "Bandit(S2)", "rule": "规则(S0)", "human": "人类回放", "buy_hold": "买入持有"}


def _svg_nav(curves: dict, dates: list, w=720, h=260, pad=40) -> str:
    """内联 SVG 净值曲线。"""
    series = {k: v for k, v in curves.items() if v}
    if not series:
        return "<p>无数据</p>"
    n = max(len(v) for v in series.values())
    allv = [x for v in series.values() for x in v]
    lo, hi = min(allv), max(allv)
    rng = (hi - lo) or 1.0

    def X(i): return pad + (w - 2 * pad) * (i / max(1, n - 1))
    def Y(val): return h - pad - (h - 2 * pad) * ((val - lo) / rng)

    parts = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;font:11px sans-serif">']
    # 网格 + y 轴标注
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = pad + (h - 2 * pad) * frac
        val = hi - rng * frac
        parts.append(f'<line x1="{pad}" y1="{y:.0f}" x2="{w-pad}" y2="{y:.0f}" stroke="{C_GRID}"/>')
        parts.append(f'<text x="{pad-5}" y="{y+3:.0f}" text-anchor="end" fill="#888">{val:,.0f}</text>')
    # x 轴首尾日期
    if dates:
        parts.append(f'<text x="{pad}" y="{h-pad+15:.0f}" fill="#888">{dates[0]}</text>')
        parts.append(f'<text x="{w-pad}" y="{h-pad+15:.0f}" text-anchor="end" fill="#888">{dates[-1]}</text>')
    # 曲线
    for k, v in series.items():
        pts = " ".join(f"{X(i):.1f},{Y(val):.1f}" for i, val in enumerate(v))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{COLORS[k]}" stroke-width="1.8"/>')
    parts.append("</svg>")
    return "".join(parts)


def _legend() -> str:
    items = "".join(
        f'<span style="margin-right:14px"><b style="color:{COLORS[k]}">━</b> {LABELS[k]}</span>'
        for k in ("bandit", "rule", "human", "buy_hold"))
    return f'<div style="margin:6px 0">{items}</div>'


def _color_val(v: float) -> str:
    c = C_UP if v >= 0 else C_DOWN
    return f'<span style="color:{c}">{v:+,.2f}</span>'


def _metrics_guide() -> str:
    """报告顶部的"指标说明"块（可折叠）。解释四条曲线/数值如何对比着读。"""
    return f"""
    <details style="margin:14px 0;padding:0 14px;border:1px solid #eee;border-radius:8px">
      <summary style="cursor:pointer;padding:10px 0;font-weight:600">指标说明 · 这几条线怎么读（点击展开）</summary>
      <div style="padding-bottom:12px;color:#333">
        <p>四条线都在<b>同一支股票、同一段测试期、同一个独立账户（本币、不换汇）</b>下跑出来，可直接横向比。</p>
        <table style="border-collapse:collapse;margin:8px 0;font-size:13px">
          <tr style="border-bottom:1px solid #ccc">
            <th style="text-align:left">名称</th><th style="text-align:left">是谁</th>
            <th style="text-align:left">怎么决策</th><th style="text-align:left">角色</th></tr>
          <tr><td><b style="color:{COLORS['buy_hold']}">买入持有</b></td><td>最笨基准</td>
            <td>测试期首日满仓买入，之后不动拿到底</td><td>及格线——跑不赢它，主动交易没意义</td></tr>
          <tr><td><b style="color:{COLORS['human']}">人类回放</b></td><td>你本人</td>
            <td>按真实下单（deals/orders）在模拟器回放成交</td><td>现状参照——你现在的真实水平</td></tr>
          <tr><td><b style="color:{COLORS['rule']}">规则(S0)</b></td><td>最简单策略</td>
            <td>预测区间下沿挂买、上沿挂卖（低买高卖）</td><td>第一道门槛——模型要先打过死规则</td></tr>
          <tr><td><b style="color:{COLORS['bandit']}">Bandit(S2)</b></td><td>学习型策略</td>
            <td>LinUCB 上下文老虎机，按特征学该挂哪个买卖动作（含 ε 探索）</td><td>主角——验证它能否真的更好</td></tr>
        </table>
        <p style="margin:8px 0 4px"><b>理想梯子（看相对值，别看绝对值）：</b>
          Bandit <b>应当 &gt;</b> 规则 <b>应当 &gt;</b> 买入持有；人类回放落在哪 = 你现在的坐标。</p>
        <ul style="margin:4px 0;padding-left:20px">
          <li><b>Bandit &gt; 买入持有</b>（总览"超越"列打 ✓）：主动交易这一支、这一段确实加价值。</li>
          <li><b>Bandit &gt; 规则</b>：学习真比死规则强，S2 才立住。</li>
          <li><b>规则 ≈ 或 &lt; 买入持有</b>：这段行情"低买高卖"本身不灵（多半单边上涨，择时不如躺着拿）。</li>
          <li><b>人类回放 vs 三者</b>：你被基准跑赢（过度交易磨损收益），还是已接近/超过规则。</li>
        </ul>
        <p style="margin:8px 0 0;color:#888;font-size:12px">两个前提：①结论只在"这支 + 这段测试期"成立，
          Bandit 不稳定地赢基准、强依赖行情，一支打 ✓ 不代表普适；②绝对收益不单独采信，只看谁相对谁高。</p>
      </div>
    </details>"""


def _verdict(bt: dict) -> str:
    """据四个策略的实际期末净值，规则化生成一段简要分析总结（非 LLM，可复现）。

    口径（与「指标说明」一致）：买入持有=地板，规则=门槛，人类=现状坐标，Bandit=被考核选手。
    只看相对值：谁超越地板、Bandit 能否打过规则、人类处在什么位置。
    """
    fe, nv, init = bt["final_equity"], bt["net_value"], bt["init_cash"]

    def ret(k):  # 相对初始资金的收益率（%），缺失返回 None
        v = fe.get(k)
        return None if v is None else (v - init) / init * 100

    rb, rr, rh, rbh = ret("bandit"), ret("rule"), ret("human"), ret("buy_hold")
    if rb is None or rbh is None:
        return "数据不足，无法生成总结。"

    parts = []
    # ① Bandit vs 买入持有（地板）
    if rb > rbh:
        parts.append(f"<b style='color:{C_UP}'>Bandit 超越买入持有</b>"
                     f"（{rb:+.1f}% vs {rbh:+.1f}%）——主动择时在这段加了价值。")
    else:
        parts.append(f"<b style='color:{C_DOWN}'>Bandit 未跑赢买入持有</b>"
                     f"（{rb:+.1f}% vs {rbh:+.1f}%）——这段多半单边行情，躺着拿更省心。")
    # ② Bandit vs 规则（学习是否优于死规则）
    if rr is not None:
        if rb > rr:
            parts.append(f"且优于规则基线（规则 {rr:+.1f}%），学习信号成立。")
        elif abs(rb - rr) < 0.5:
            parts.append(f"与规则基线基本持平（规则 {rr:+.1f}%），学习暂未体现增量。")
        else:
            parts.append(f"但<b style='color:{C_DOWN}'>反被规则基线超过</b>（规则 {rr:+.1f}%），"
                         f"bandit 在此样本未学到更优策略。")
    # ③ 人类回放定位
    if rh is not None:
        peers = sorted([("Bandit", rb), ("规则", rr if rr is not None else -1e9),
                        ("买入持有", rbh)], key=lambda t: -t[1])
        better = [name for name, r in peers if r > rh]
        if not better:
            parts.append(f"人类回放（{rh:+.1f}%）此段领先全部策略。")
        elif len(better) == 3:
            parts.append(f"人类回放（{rh:+.1f}%）落后全部基线——这段真实操作可能过度交易、磨损收益。")
        else:
            parts.append(f"人类回放（{rh:+.1f}%）被 {'、'.join(better)} 跑赢。")
    # ④ 达成净值（目标函数）一句带过
    parts.append(f"达成交易净值(卖−买) Bandit {nv['bandit']:+,.0f}、人类 {nv['human']:+,.0f}。")

    return " ".join(parts)


def _stock_section(code: str, bt: dict, pred: dict) -> str:
    fe, nv = bt["final_equity"], bt["net_value"]
    init = bt["init_cash"]
    rows = ""
    for k in ("bandit", "rule", "human", "buy_hold"):
        eq = fe.get(k)
        if eq is None:
            continue
        ret = (eq - init) / init * 100
        rows += (f"<tr><td>{LABELS[k]}</td><td style='text-align:right'>{eq:,.0f}</td>"
                 f"<td style='text-align:right'>{_color_val(ret)}%</td></tr>")
    pred_html = (f"<b>{code}</b> 截至 {pred['as_of']} 收盘 {pred['close']:,.2f} → "
                 f"次日预测区间 <b style='color:{C_DOWN}'>{pred['L_hat']:,.2f}</b> ~ "
                 f"<b style='color:{C_UP}'>{pred['H_hat']:,.2f}</b>（宽 {pred['width_pct']}%）")
    return f"""
    <section style="margin:26px 0;padding:16px;border:1px solid #eee;border-radius:8px">
      <h3>{html.escape(code)}</h3>
      <p>{pred_html}</p>
      {_legend()}
      {_svg_nav(bt['nav_curves'], bt['nav_dates'])}
      <table style="border-collapse:collapse;margin-top:10px;min-width:340px">
        <tr style="border-bottom:1px solid #ccc"><th style="text-align:left">策略</th>
          <th style="text-align:right">期末净值</th><th style="text-align:right">收益率</th></tr>
        {rows}
      </table>
      <p style="color:#888;font-size:12px">测试 {bt['n_test_days']} 日，初始 {init:,.0f}，后端 {bt['backend']}。
         达成净值(卖−买)：Bandit {_color_val(nv['bandit'])} / 规则 {_color_val(nv['rule'])} / 人类 {_color_val(nv['human'])}</p>
      <div style="margin-top:10px;padding:10px 12px;background:#fafafa;border-left:3px solid #ccc;
           border-radius:4px;font-size:13px;line-height:1.7">
        <b>分析总结：</b>{_verdict(bt)}</div>
    </section>"""


def build_report(out_dir: Path | None = None, cfg: BTConfig | None = None) -> Path:
    cfg = cfg or BTConfig()
    today = dt.date.today().isoformat()
    out_dir = out_dir or (mlcfg.REPORTS_DIR / today)
    out_dir.mkdir(parents=True, exist_ok=True)

    sections, summary_rows = [], ""
    for code in mlcfg.TARGETS:
        daily = mldata.load_daily(code)
        bt = run_backtest(code, cfg)
        if "error" in bt:
            continue
        lo_a, hi_a = mlcfg.alpha_for(code)  # 按股自适应分位（收窄区间）
        pred = predict_next_day(daily, seed=cfg.seed,
                                high_alpha=hi_a, low_alpha=lo_a)
        sections.append(_stock_section(code, bt, pred))
        fe = bt["final_equity"]
        beat = "✓" if (fe.get("bandit") or 0) > (fe.get("buy_hold") or 0) else "✗"
        summary_rows += (f"<tr><td>{code}</td>"
                         f"<td style='text-align:right'>{fe.get('bandit',0):,.0f}</td>"
                         f"<td style='text-align:right'>{fe.get('buy_hold',0):,.0f}</td>"
                         f"<td style='text-align:center'>{beat}</td>"
                         f"<td>{pred['L_hat']:,.2f} ~ {pred['H_hat']:,.2f}</td></tr>")

    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>myStock ML 回测报告 {today}</title>
<style>body{{font:14px/1.6 -apple-system,sans-serif;max-width:840px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:20px}} table{{font-size:13px}} td,th{{padding:4px 10px}}</style></head><body>
<h1>myStock ML 回测报告 · {today}</h1>
<p style="color:#888">3 美股(USD) + 3 港股(HKD)，各股独立账户本币计价 · 目标=最大化达成交易净值 · 红涨绿跌 · 离线产物（不碰 web）</p>
{_metrics_guide()}
<h2>总览：Bandit vs 买入持有 + 次日预测</h2>
<table style="border-collapse:collapse;min-width:560px">
  <tr style="border-bottom:1px solid #ccc"><th style="text-align:left">标的</th>
    <th style="text-align:right">Bandit 期末</th><th style="text-align:right">买入持有</th>
    <th>超越</th><th style="text-align:left">次日预测区间</th></tr>
  {summary_rows}
</table>
<p style="color:#888;font-size:12px">"超越"= Bandit 期末净值是否高于买入持有。结论看相对值，绝对收益不单独采信。</p>
{''.join(sections)}
<hr><p style="color:#aaa;font-size:12px">生成于 {dt.datetime.now():%Y-%m-%d %H:%M}。完整方案见 docs/ML_PLAN.md，速览见 docs/ML_OVERVIEW.md。</p>
</body></html>"""

    index = out_dir / "index.html"
    index.write_text(page, encoding="utf-8")
    # latest.html 指向最新
    latest = mlcfg.REPORTS_DIR / "latest.html"
    latest.write_text(page, encoding="utf-8")
    return index


if __name__ == "__main__":
    p = build_report()
    print(f"报告已生成：{p}")
    print(f"最新副本：{mlcfg.REPORTS_DIR / 'latest.html'}")
