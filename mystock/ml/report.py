"""P5 — 每日回测 HTML 报告（自包含单文件，归档到 data/ml/reports/<date>/）。

无外部依赖：纯 Python 生成 HTML + 内联 SVG 净值曲线（无 JS 库）。
配色沿用项目"红涨绿跌"（CLAUDE.md 约定）。绝不碰 web / 生产库。
运行：python -m mystock.ml.report  → 生成当日报告 + 更新 latest.html
"""
from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass, replace
from pathlib import Path

from . import backfill as mlbackfill
from . import config as mlcfg
from . import data as mldata
from . import db as mldb
from . import review as mlreview
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


def _fmt_eq(v) -> str:
    """总览期末净值格式化：None/NaN 显示为「—」而非裸 nan（防脏数据漏到展示层）。"""
    if v is None or v != v:   # None 或 NaN
        return "—"
    return f"{v:,.0f}"


def _fmt_pct(v) -> str:
    """比例(0~1)格式化为百分比；None/NaN → 「—」。"""
    if v is None or v != v:
        return "—"
    return f"{v * 100:.0f}%"


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


def _fmt_ic(v) -> str:
    """IC 值格式化：None/NaN → 「—」，否则带符号 3 位。"""
    if v is None or v != v:
        return "—"
    return f"{v:+.3f}"


def _two_level_verdict(bt: dict) -> str:
    """借鉴③（Plan §3.3）：两级评估——先信号层，再策略层，规则化生成分诊结论。

    宽度优先（分位模型的忠实度量）：
      - 宽度 IC 高 + 净值输 → 区间跟得住振幅但没换成钱 → 执行/挂价档是瓶颈（攻决策层）。
      - 宽度 IC 低 → 区间连振幅都跟不住 → 攻预测层/因子（即借鉴①）。
      - 方向 IC ≈ 0 → 只说明无方向信息（分位模型本就没学方向），单凭它不下"信号无效"结论。
    这句诊断直接告诉团队下一步该往哪使劲——两级评估最大的价值。
    """
    s = bt.get("signal") or {}
    w_ic, m_ic = s.get("width_ic"), s.get("mid_ic")
    fe, init = bt["final_equity"], bt["init_cash"]
    b, bh = fe.get("bandit"), fe.get("buy_hold")
    beat = (b is not None and b == b and bh is not None and bh == bh and b > bh)

    if w_ic is None or w_ic != w_ic:
        sig_line = "信号层：宽度 IC 不可得（样本不足）。"
        head = ""
    else:
        strong = w_ic >= 0.15   # 单标的时间轴 IC 天然弱，0.15 已算有跟踪力
        if strong and not beat:
            head = (f"<b style='color:{C_UP}'>信号跟得住振幅但没换成钱</b>"
                    f"（宽度 IC={_fmt_ic(w_ic)}，净值未超买入持有）→ "
                    f"<b>瓶颈在执行/挂价档</b>，下一步攻决策层（挂价、数量档、reward）。")
        elif strong and beat:
            head = (f"<b style='color:{C_UP}'>信号有跟踪力且已换成超额</b>"
                    f"（宽度 IC={_fmt_ic(w_ic)}，净值超买入持有）→ 预测+执行链路本段成立。")
        else:
            head = (f"<b style='color:{C_DOWN}'>信号连振幅都跟不住</b>"
                    f"（宽度 IC={_fmt_ic(w_ic)}）→ <b>先攻预测层/因子</b>（借鉴①的因子库），"
                    f"此时调执行层是缘木求鱼。")
        sig_line = f"信号层：宽度 IC={_fmt_ic(w_ic)}（主）、中点 IC={_fmt_ic(m_ic)}（次）。"

    # 方向 IC 的诚实注解（避免误判"信号无效"）
    note = ""
    if m_ic is not None and m_ic == m_ic and abs(m_ic) < 0.05:
        note = ("　<span style='color:#888'>中点 IC≈0 属预期——分位模型没学方向，"
                "不据此判\"信号无效\"，以宽度 IC 为准。</span>")
    return sig_line + " " + head + note


C_SLATE = "#5b6470"   # 中性文字（略偏蓝，与报告既有 #2a6fd8 同族，非纯灰）
C_PANEL = "#f7f8fa"   # 面板底
C_LINE = "#e3e6ea"    # 分隔线
C_BOTH = "#8a4fd8"    # 双破（红绿都不合适，用紫作第三态）

# 复盘面板样式（作用域限定 .rv-* ，不影响报告其余部分）。
# tab 用「radio + :checked ~ 兄弟选择器」纯 CSS 实现——报告是零 JS 的自包含单文件，
# 加 JS 会破坏这个性质（离线打开、邮件转发、归档都可能禁脚本），故不引入。
_REVIEW_CSS = f"""
.rv{{margin:14px 0 8px}}
/* 6 个 tab 单行放不下时横向滚动，好过折行——折行会让末个 tab 孤零零掉到第二行 */
.rv-tabs{{display:flex;gap:2px;margin-bottom:0;border-bottom:1px solid {C_LINE};
  padding-bottom:0;overflow-x:auto;scrollbar-width:thin}}
.rv input[type=radio]{{position:absolute;opacity:0;pointer-events:none}}
.rv-tab{{display:inline-flex;align-items:baseline;gap:5px;cursor:pointer;padding:7px 10px;
  border:1px solid transparent;border-bottom:none;border-radius:6px 6px 0 0;white-space:nowrap;
  font-size:12.5px;color:{C_SLATE};line-height:1.3;user-select:none;position:relative;top:1px}}
.rv-tab:hover{{background:{C_PANEL}}}
.rv-tab .rv-rate{{font-size:11px;font-variant-numeric:tabular-nums;opacity:.75}}
/* 选中态：标签高亮 + 与下方面板连成一体 */
.rv-r1:checked ~ .rv-tabs label[for=rv-t1],
.rv-r2:checked ~ .rv-tabs label[for=rv-t2],
.rv-r3:checked ~ .rv-tabs label[for=rv-t3],
.rv-r4:checked ~ .rv-tabs label[for=rv-t4],
.rv-r5:checked ~ .rv-tabs label[for=rv-t5],
.rv-r6:checked ~ .rv-tabs label[for=rv-t6]{{background:#fff;border-color:{C_LINE};
  color:#222;font-weight:600;box-shadow:0 -2px 0 #2a6fd8 inset}}
.rv input[type=radio]:focus-visible ~ .rv-tabs label{{outline:2px solid #2a6fd8;outline-offset:2px}}
.rv-panel{{display:none;border:1px solid {C_LINE};border-top:none;border-radius:0 0 8px 8px;padding:14px 16px}}
.rv-r1:checked ~ .rv-body .rv-p1,
.rv-r2:checked ~ .rv-body .rv-p2,
.rv-r3:checked ~ .rv-body .rv-p3,
.rv-r4:checked ~ .rv-body .rv-p4,
.rv-r5:checked ~ .rv-body .rv-p5,
.rv-r6:checked ~ .rv-body .rv-p6{{display:block}}
/* 汇总条：先给结论，再给逐条明细 */
.rv-sum{{display:flex;flex-wrap:wrap;gap:18px;align-items:baseline;
  background:{C_PANEL};border-radius:6px;padding:10px 14px;margin-bottom:12px}}
.rv-stat{{display:flex;flex-direction:column;gap:2px}}
.rv-stat b{{font-size:17px;font-variant-numeric:tabular-nums;line-height:1.2}}
.rv-stat span{{font-size:11px;color:{C_SLATE};letter-spacing:.03em}}
.rv-diag{{flex:1 1 220px;font-size:12px;color:{C_SLATE};line-height:1.55;min-width:200px}}
.rv-scroll{{overflow-x:auto}}
table.rv-t{{border-collapse:collapse;width:100%;font-size:13px;
  font-variant-numeric:tabular-nums;white-space:nowrap}}
table.rv-t th{{text-align:right;font-weight:600;color:{C_SLATE};font-size:11px;
  letter-spacing:.04em;border-bottom:1px solid {C_LINE};padding:4px 8px}}
table.rv-t th:first-child{{text-align:left}}
table.rv-t td{{padding:5px 8px;text-align:right;border-bottom:1px solid #f0f2f4}}
/* 结果列给足宽度：标签形如「上破 12.34%」，挤窄会被裁掉百分号 */
table.rv-t th:last-child,table.rv-t td:last-child{{width:1%;padding-right:2px}}
table.rv-t td:first-child{{text-align:left;color:{C_SLATE}}}
table.rv-t tr:last-child td{{border-bottom:none}}
/* 区间带图：预测带（浅）与实际带（深）叠画，戳出部分自然露在外面 */
/* display:inline-block 必需——span 默认 inline，宽高不生效，带图会塌成 0×0 */
.rv-bar{{display:inline-block;position:relative;width:150px;height:15px;
  background:#eef0f3;border-radius:3px;overflow:hidden;vertical-align:middle}}
.rv-bar i{{position:absolute;top:0;bottom:0;display:block}}
.rv-bar .rv-pred{{background:#cfd6e0;border-radius:2px}}
/* 实际带的 background 由行内样式按结果着色（命中灰 / 上破红 / 下破绿 / 双破紫） */
.rv-bar .rv-act{{border-radius:2px;top:4px;bottom:4px;box-shadow:0 0 0 1px #fff}}
.rv-tag{{display:inline-block;min-width:74px;text-align:center;font-size:11px;
  padding:2px 8px;border-radius:20px;font-weight:600}}
.rv-more{{margin-top:10px}}
.rv-more summary{{cursor:pointer;font-size:12px;color:#2a6fd8;padding:4px 0;
  list-style:none;display:inline-flex;align-items:center;gap:5px}}
.rv-more summary::-webkit-details-marker{{display:none}}
.rv-more summary::before{{content:"▸";font-size:10px;transition:transform .15s}}
.rv-more[open] summary::before{{transform:rotate(90deg)}}
/* 折叠态显示「展开」文案，展开态显示「收起」。两条规则的目标元素不同，
   互不覆盖——写成 [open]/:not([open]) 各自 hide 同一元素会因特异性相同而按
   源码序决出胜负，导致文案反转。 */
.rv-more-on{{display:none}}
.rv-more[open] summary .rv-more-off{{display:none}}
.rv-more[open] summary .rv-more-on{{display:inline}}
.rv-note{{color:#8b929c;font-size:11.5px;line-height:1.6;margin:10px 0 0}}
.rv-key{{display:inline-flex;align-items:center;gap:5px;margin-right:14px;color:{C_SLATE}}}
.rv-key i{{display:inline-block;width:16px;height:9px;border-radius:2px}}
@media (prefers-reduced-motion:reduce){{.rv-more summary::before{{transition:none}}}}
"""


def _rv_bar(r: dict, lo: float, hi: float) -> str:
    """一行的区间带图：预测带（浅底）vs 实际带（实色），共用该股价格标尺。

    实际带按结果着色——命中=中性灰，上破=红，下破=绿（红涨绿跌），双破=紫。
    只画"带子叠带子"是不够的：命中与戳出在形状上太像，一眼扫过去分不出，
    故用颜色承担"结果"这一维，形状承担"位置/幅度"这一维。
    """
    span = (hi - lo) or 1.0

    def pct(v):
        return max(0.0, min(100.0, (v - lo) / span * 100))

    p1, p2 = pct(r["l_hat"]), pct(r["h_hat"])
    a1, a2 = pct(r["actual_low"]), pct(r["actual_high"])
    c = {"above": C_UP, "below": C_DOWN, "both": C_BOTH}.get(r["miss_side"], C_SLATE)
    # 无障碍：带图是纯装饰（同行已有数值与结果标签），对读屏隐藏，避免重复播报
    return (f'<span class="rv-bar" aria-hidden="true">'
            f'<i class="rv-pred" style="left:{p1:.1f}%;width:{max(0.8, p2 - p1):.1f}%"></i>'
            f'<i class="rv-act" style="left:{a1:.1f}%;width:{max(1.2, a2 - a1):.1f}%;'
            f'background:{c}"></i></span>')


def _rv_rows(rs: list, lo: float, hi: float) -> str:
    out = ""
    for r in rs:
        if r["status"] == "hit":
            tag = f'<span class="rv-tag" style="background:#eef1f4;color:{C_SLATE}">✓ 命中</span>'
        else:
            side = {"above": "上破", "below": "下破", "both": "双破"}[r["miss_side"]]
            # 上破=真实涨超上沿→红；下破=真实跌破下沿→绿（红涨绿跌）
            c = {"above": C_UP, "below": C_DOWN, "both": C_BOTH}[r["miss_side"]]
            tag = (f'<span class="rv-tag" style="background:{c}14;color:{c}">'
                   f'{side} {r["miss_pct"]:.2f}%</span>')
        out += (f"<tr><td>{r['as_of']} → <b style='color:#222'>{r['next_day']}</b></td>"
                f"<td>{r['l_hat']:,.2f} ~ {r['h_hat']:,.2f}</td>"
                f"<td>{r['actual_low']:,.2f} ~ {r['actual_high']:,.2f}</td>"
                f"<td style='text-align:center'>{_rv_bar(r, lo, hi)}</td>"
                f"<td style='text-align:center'>{tag}</td></tr>")
    return out


def _rv_diagnosis(s: dict) -> str:
    """据上破/下破的失衡程度给一句诊断——偏置可修，宽度不足是另一回事。"""
    a, b = s["n_miss_above"], s["n_miss_below"]
    n = a + b
    if not n:
        return "全部命中，无戳出样本。"
    if a >= b * 2 and a >= 3:
        return (f"<b style='color:{C_UP}'>上破为主（{a} vs {b}）</b>：区间系统性偏低，"
                f"属可修的<b>偏置</b>——上沿分位或 CQR 目标覆盖率可调。")
    if b >= a * 2 and b >= 3:
        return (f"<b style='color:{C_DOWN}'>下破为主（{b} vs {a}）</b>：区间系统性偏高，"
                f"属可修的<b>偏置</b>——下沿分位或 CQR 目标覆盖率可调。")
    return (f"上下破基本均衡（{a} / {b}）：不是方向偏置，而是<b>宽度不足</b>——"
            f"要提命中率需放宽区间（调高目标覆盖率），代价是区间变宽。")


def _review_panel(reviews: dict, recent: int = 7) -> str:
    """「近期预测复盘」面板：按股 tab 切换，默认展示最近 recent 个交易日。

    数据源 = ml_predictions（报告实时留档 + 历史 HTML 回填），**不是**回测里
    walk-forward 的历史预测——那是另一个模型（只用前 60% 数据 fit）。这里展示的
    才是"当时线上真的这么说"的预测，故命中率通常低于总览里的回测命中率。

    纯 CSS 交互（radio tab + details 展开），不引入 JS——保持报告零依赖、
    离线可读的既有性质。
    """
    if not reviews:
        return ""
    # 只保留有已结算样本的股票；tab 序号按 TARGETS 顺序稳定
    items = []
    for code in mlcfg.TARGETS:
        rs = [r for r in reviews.get(code, []) if r["status"] in ("hit", "miss")]
        if rs:
            items.append((code, rs))
    if not items:
        return ""

    radios, tabs, panels = "", "", ""
    for n, (code, rs) in enumerate(items, start=1):
        s = mlreview.summarize(reviews.get(code, []))
        checked = " checked" if n == 1 else ""
        radios += (f'<input class="rv-r{n}" type="radio" name="rv-tab" '
                   f'id="rv-t{n}"{checked}>')
        rate = _fmt_pct(s["hit_rate"])
        tabs += (f'<label class="rv-tab" for="rv-t{n}">{html.escape(code)}'
                 f'<span class="rv-rate">{rate}</span></label>')

        # 价格标尺取该股全部样本的极值（预测与实际同尺，跨行可比）
        lo = min(min(r["l_hat"], r["actual_low"]) for r in rs)
        hi = max(max(r["h_hat"], r["actual_high"]) for r in rs)
        head = ("<tr><th>基准日 → 次日</th><th>预测区间</th><th>实际 低~高</th>"
                "<th style='text-align:center'>区间对照</th>"
                "<th style='text-align:center'>结果</th></tr>")
        # rs 按 as_of 升序（review_predictions 的口径）：末尾 recent 条即最近几日。
        # 展示时倒序——最新的排最上面，符合"先看最近发生了什么"的阅读习惯。
        # 只在展示层倒序，不动 review_predictions 的升序返回（那是数据口径，
        # 命中率统计、价格标尺等都依赖它稳定）。
        latest, older = rs[-recent:][::-1], rs[:-recent][::-1]
        more = ""
        if older:
            more = f"""
        <details class="rv-more"><summary>
          <span class="rv-more-off">展开更早的 {len(older)} 条（共 {len(rs)} 条）</span>
          <span class="rv-more-on">收起更早记录</span></summary>
          <div class="rv-scroll"><table class="rv-t">{head}{_rv_rows(older, lo, hi)}</table></div>
        </details>"""
        pend = sum(1 for r in reviews.get(code, []) if r["status"] == "pending")
        pend_txt = (f"　<span style='color:#8b929c'>另有 {pend} 条待结算"
                    f"（次日行情尚未走出）</span>" if pend else "")
        avg = s["avg_miss_pct"]
        panels += f"""
      <div class="rv-panel rv-p{n}">
        <div class="rv-sum">
          <div class="rv-stat"><b>{rate}</b><span>命中率</span></div>
          <div class="rv-stat"><b>{s['n_settled']}</b><span>已结算</span></div>
          <div class="rv-stat"><b><span style="color:{C_UP}">{s['n_miss_above']}</span>
            <span style="color:#c9ced6">/</span>
            <span style="color:{C_DOWN}">{s['n_miss_below']}</span></b><span>上破 / 下破</span></div>
          <div class="rv-stat"><b>{f'{avg:.2f}%' if avg is not None else '—'}</b><span>平均戳出</span></div>
          <div class="rv-diag">{_rv_diagnosis(s)}</div>
        </div>
        <div class="rv-scroll"><table class="rv-t">{head}{_rv_rows(latest, lo, hi)}</table></div>
        <p class="rv-note" style="margin-top:8px">显示最近 {len(latest)} 条{pend_txt}</p>
        {more}
      </div>"""

    return f"""
<h2>近期预测复盘：当时说的区间 vs 实际走出来的高低</h2>
<p style="color:#666;font-size:12px">下表是<b>过去报告真实给出</b>的次日区间（全历史 fit 的线上预测，
留档于 ml_predictions），与该次日真实高/低的逐条对照。<b>与总览「命中率」不是同一口径</b>——总览那个
来自回测（walk-forward 模型，训练截止在很早以前），这里是线上预测的实际表现。</p>
<div class="rv">{radios}
  <div class="rv-tabs">{tabs}</div>
  <div class="rv-body">{panels}</div>
</div>
<p class="rv-note">
<span class="rv-key"><i style="background:#cfd6e0"></i>预测区间</span>
<span class="rv-key"><i style="background:{C_SLATE}"></i>实际·命中</span>
<span class="rv-key"><i style="background:{C_UP}"></i>实际·上破</span>
<span class="rv-key"><i style="background:{C_DOWN}"></i>实际·下破</span>
<span class="rv-key"><i style="background:{C_BOTH}"></i>实际·双破</span>
<br>「区间对照」浅色条=预测区间，实色条=当日实际高低（按结果着色），同股共用价格标尺——
实际条探出浅色条的部分即戳出。「上破」=真实最高涨超预测上沿，「下破」=真实最低跌破预测下沿；
单边戳出即算未命中，不做部分命中粉饰。「平均戳出」=未命中时戳出幅度 / 基准日收盘，取上下较大侧。
报告非每个交易日都生成，故序列有洞，逐行标注基准日与次日，勿当连续序列读。</p>"""


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
      <p style="color:#888;font-size:12px">测试 {bt['n_test_days']} 日，初始 {init:,.0f}，
         区间命中 {_fmt_pct(bt.get('interval_hit_rate'))}，后端 {bt['backend']}。
         达成净值(卖−买)：Bandit {_color_val(nv['bandit'])} / 规则 {_color_val(nv['rule'])} / 人类 {_color_val(nv['human'])}</p>
      <div style="margin-top:10px;padding:10px 12px;background:#fafafa;border-left:3px solid #ccc;
           border-radius:4px;font-size:13px;line-height:1.7">
        <b>分析总结：</b>{_verdict(bt)}</div>
      <div style="margin-top:8px;padding:10px 12px;background:#f4f8fb;border-left:3px solid #2a6fd8;
           border-radius:4px;font-size:13px;line-height:1.7">
        <b>两级评估（信号 vs 执行）：</b>{_two_level_verdict(bt)}</div>
    </section>"""


@dataclass
class _ReportCfg:
    """build_report 的开关（透传到 BTConfig / predict_next_day）。

    标准口径：超额-bandit + CQR 校准（建议2，提升区间命中率）+ purged 隔离带（借鉴②）。
    风险调整 reward / HMM regime 软切换（原 Tier1 建议1+3）未通过时段稳健性检验、
    已移除，详见 docs/ML_TIER1_ROBUSTNESS.md。
    """
    conformal: bool = True
    target_coverage: float = 0.70   # 兜底；实际按股走 mlcfg.coverage_for(code)


def _mode_banner(cfg: BTConfig) -> str:
    """报告头部一行：当前 reward / 预测口径（透明化标准开关）。"""
    tags = [f"reward={'excess' if cfg.excess_reward else 'raw'}"]
    tags.append(f"CQR={'on' if cfg.conformal else 'off'}"
                + (f"(目标{cfg.target_coverage:.0%})" if cfg.conformal else ""))
    tags.append(f"purge={'on(隔离带)' if getattr(cfg, 'purged', False) else 'off'}")
    return " · ".join(tags)


def build_report(out_dir: Path | None = None, cfg: BTConfig | None = None,
                 rcfg: _ReportCfg | None = None, *, db_path=None, clock=None) -> Path | None:
    from . import sessions
    from .pipeline import write_status
    clock = clock or sessions.utc_now
    statuses = []
    from . import runs, versions
    run_manifest = manifest_path = None
    rcfg = rcfg or _ReportCfg()
    cfg = cfg or BTConfig(
        conformal=rcfg.conformal, target_coverage=rcfg.target_coverage,
    )
    today = dt.date.today().isoformat()

    # 冻结本次输入；历史 HTML 仅经显式离线导入，不混入 live 留档。
    mldb.init_ml_db(db_path)
    run_manifest, manifest_path = runs.start(db_path)
    input_db = run_manifest["input_path"]
    run_manifest['seed'] = cfg.seed
    out_dir = out_dir or (mlcfg.REPORTS_DIR / 'runs' / run_manifest['run_id'])
    out_dir.mkdir(parents=True, exist_ok=True)

    sections, summary_rows, pred_rows = [], "", []
    for code in mlcfg.TARGETS:
        try:
            daily = sessions.prepare_daily(mldata.load_daily(code, input_db), code, clock())
        except sessions.Unavailable as e:
            statuses.append({'code':code, 'status':e.status})
            continue
        # 按股自适应 CQR 目标覆盖率（收窄区间；与 ALPHA_BY_CODE 同模式）
        cov = mlcfg.coverage_for(code)
        cfg_i = cfg if cfg.target_coverage == cov else replace(cfg, target_coverage=cov)
        try:
            bt = run_backtest(code, cfg_i, db_path=input_db, daily=daily)
        except Exception as e:
            statuses.append({'code':code,'status':'failed','error':type(e).__name__})
            continue
        if "error" in bt:
            statuses.append({"code":code,"status":"failed","error":str(bt["error"])})
            continue
        lo_a, hi_a = mlcfg.alpha_for(code)  # 按股自适应分位（收窄区间）
        try:
            pred = predict_next_day(daily, seed=cfg.seed, code=code, clock=clock,
                                    high_alpha=hi_a, low_alpha=lo_a,
                                    conformal=cfg.conformal, target_coverage=cov)
        except sessions.Unavailable as e:
            statuses.append({'code':code,'status':e.status})
            continue
        except Exception as e:
            statuses.append({'code':code,'status':'failed','error':type(e).__name__})
            continue
        statuses.append({'code':code,'status':'generated','target_session':pred['target_session']})
        sections.append(_stock_section(code, bt, pred))
        # 本次预测按 run 追加；同日多版本并存。
        pred_rows.append({
            "code": code, "as_of": pred["as_of"], "close": pred["close"],
            "l_hat": pred["L_hat"], "h_hat": pred["H_hat"],
            "width_pct": pred["width_pct"],
            "low_alpha": lo_a, "high_alpha": hi_a,
            "conformal": int(bool(pred["conformal"])), "q_ret": pred["q_ret"],
            "target_coverage": pred["target_coverage"],
            "backend": bt["backend"], "source": "live",
            "target_session": pred["target_session"],
            "run_id": run_manifest["run_id"], "manifest_path": str(manifest_path.resolve()),
            "generated_at": clock().isoformat(), "decision_at": clock().isoformat(),
        })
        fe = bt["final_equity"]
        b, bh = fe.get("bandit"), fe.get("buy_hold")
        # NaN/None 时不下「超越」结论（nan 比较恒 False，会误判为 ✗）
        beat = "—" if (b is None or b != b or bh is None or bh != bh) else ("✓" if b > bh else "✗")
        w_ic = (bt.get("signal") or {}).get("width_ic")
        summary_rows += (f"<tr><td>{code}</td>"
                         f"<td style='text-align:right'>{_fmt_eq(b)}</td>"
                         f"<td style='text-align:right'>{_fmt_eq(bh)}</td>"
                         f"<td style='text-align:center'>{beat}</td>"
                         f"<td style='text-align:right'>{_fmt_ic(w_ic)}</td>"
                         f"<td>{pred['L_hat']:,.2f} ~ {pred['H_hat']:,.2f}</td>"
                         f"<td style='text-align:right'>{pred['width_pct']:.2f}%</td>"
                         f"<td style='text-align:right'>{_fmt_pct(bt.get('interval_hit_rate'))}</td></tr>")

    # 写入本次预测 + 读回全部留档做复盘（写在渲染前，故当日预测也进表，
    # 但其次日尚未走出 → review 判为 pending，不进复盘表、不污染命中率）
    # Recheck all targets after all markets trained; never shift stale targets.
    expired = {p['code'] for p in pred_rows if clock() >= sessions.session(p['code'],p['target_session'])['deadline']}
    for st in statuses:
        if st['code'] in expired: st['status']='missed_deadline'
    pred_rows = [p for p in pred_rows if p['code'] not in expired]
    if expired:
        import re
        for code in expired: summary_rows = re.sub(r"<tr><td>" + re.escape(code) + r"</td>.*?</tr>", "", summary_rows)
    if expired: sections = [section for section in sections if not any(c in section for c in expired)]
    with mldb.get_ml_connection(db_path) as status_conn:
        for st in statuses: mldb.log_sync(status_conn,'prediction_guard',symbol=st['code'],status=st['status'])
    if not pred_rows:
        runs.finish(run_manifest, manifest_path, pred_rows, statuses)
        write_status(statuses, None, db_path)
        return None
    conn = mldb.get_ml_connection(db_path)
    try:
        if pred_rows:
            mldb.upsert_predictions(conn, pred_rows)
            mldb.log_sync(conn, "predictions", row_count=len(pred_rows),
                          range_end=today)
        reviews = {
            code: mlreview.review_predictions(
                versions.load(conn, code, source="live"), mldata.load_daily(code, db_path))
            for code in mlcfg.TARGETS
        }
    finally:
        conn.close()

    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>myStock ML 回测报告 {today}</title>
<style>:root{{color-scheme:light}}
/* 报告是浅色单主题：必须显式给 background，否则在深色模式的浏览器/客户端里
   会借用宿主的深色底，配上 color:#222 变成"深底深字"读不了。 */
body{{font:14px/1.6 -apple-system,sans-serif;max-width:840px;margin:24px auto;padding:0 16px;
  color:#222;background:#fff}}
h1{{font-size:20px}} table{{font-size:13px}} td,th{{padding:4px 10px}}
{_REVIEW_CSS}</style></head><body>
<h1>myStock ML 回测报告 · {today}</h1>
<p>本次状态：{statuses}</p>
<p style="color:#888">3 美股(USD) + 3 港股(HKD)，各股独立账户本币计价 · 目标=最大化达成交易净值 · 红涨绿跌 · 离线产物（不碰 web）</p>
<p style="color:#666;font-size:12px">口径：{_mode_banner(cfg)}</p>
{_metrics_guide()}
<h2>总览：Bandit vs 买入持有 + 次日预测</h2>
<table style="border-collapse:collapse;min-width:560px">
  <tr style="border-bottom:1px solid #ccc"><th style="text-align:left">标的</th>
    <th style="text-align:right">Bandit 期末</th><th style="text-align:right">买入持有</th>
    <th>超越</th><th style="text-align:right">宽度IC</th><th style="text-align:left">次日预测区间</th>
    <th style="text-align:right">区间宽</th><th style="text-align:right">命中率</th></tr>
  {summary_rows}
</table>
<p style="color:#888;font-size:12px">"超越"= Bandit 期末净值是否高于买入持有。"宽度IC"= 预测区间宽 vs 真实次日振幅的
时间轴 Spearman 相关（信号层主指标，单标的天然偏弱，仅作诊断非门槛）。"命中率"= 测试窗内次日真实高/低
全落进预测区间的比例（分位收窄的诚实代价，~50% 属预期，见指标说明）。结论看相对值，绝对收益不单独采信。</p>
{_review_panel(reviews)}
{''.join(sections)}
<hr><p style="color:#aaa;font-size:12px">生成于 {dt.datetime.now():%Y-%m-%d %H:%M}。完整方案见 docs/ML_PLAN.md，速览见 docs/ML_OVERVIEW.md，新算法见 docs/ML_ALGORITHM_PROPOSAL.md。</p>
</body></html>"""

    index = out_dir / "index.html"
    index.write_text(page, encoding="utf-8")
    # latest.html 指向最新
    latest = mlcfg.REPORTS_DIR / "latest.html"
    latest.write_text(page, encoding="utf-8")
    import hashlib
    run_manifest['artifact_path'] = str(index.resolve())
    run_manifest['artifact_sha256'] = hashlib.sha256(index.read_bytes()).hexdigest()
    runs.finish(run_manifest, manifest_path, pred_rows, statuses)
    write_status(statuses, index, db_path)
    return index


if __name__ == "__main__":
    p = build_report()
    print(f"报告结果：{p or 'all_skipped'}")
    from .pipeline import exit_code
    if exit_code(): raise SystemExit(1)
    print(f"最新副本：{mlcfg.REPORTS_DIR / 'latest.html'}")
