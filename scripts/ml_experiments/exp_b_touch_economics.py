"""实验 B（docs/ML_UPGRADE_PLAN.md §3.3 原脚本，只读）：用留档预测看挂单经济性——单边成交后的 1/5 日后续收益（逆向选择）、多日轮回完成率。"""
import numpy as np
from mystock.ml import config as mlcfg, data as mldata, db as mldb
from mystock.ml.simulator import match_limit_order, BUY, SELL
conn = mldb.get_ml_connection()
for code in mlcfg.TARGETS:
    daily = mldata.load_daily(code); dates = list(daily["date"]); idx = {d:i for i,d in enumerate(dates)}
    close = daily["close"].values
    bars = mldata.intraday_bars_by_day(code)
    preds = {p["as_of"]: p for p in mldb.load_predictions(conn, code)}
    buys, sells, rt = [], [], []
    for as_of, p in sorted(preds.items()):
        i = idx.get(as_of)
        if i is None or i+1 >= len(dates) or not bars.get(dates[i+1]): continue
        d1 = dates[i+1]
        fb = match_limit_order(BUY, p["l_hat"], bars[d1]); fs = match_limit_order(SELL, p["h_hat"], bars[d1])
        if fb.filled:
            r1 = close[i+1]/fb.fill_price-1; r5 = (close[min(i+5,len(close)-1)]/fb.fill_price-1)
            buys.append((r1, r5))
            # 多日轮回：买入后逐日用「当日预测 Ĥ」挂卖，直到成交（上限 20 交易日）
            done = None
            for k in range(i+1, min(i+21, len(dates)-1)):
                pk = preds.get(dates[k]); nb = bars.get(dates[k+1])
                if pk and nb:
                    f = match_limit_order(SELL, pk["h_hat"], nb)
                    if f.filled: done = (k+1-(i+1), f.fill_price/fb.fill_price-1); break
            rt.append(done)
        if fs.filled:
            r1 = fs.fill_price/close[i+1]-1; r5 = fs.fill_price/close[min(i+5,len(close)-1)]-1
            sells.append((r1, r5))
    b = np.array(buys) if buys else np.zeros((0,2)); s = np.array(sells) if sells else np.zeros((0,2))
    comp = [x for x in rt if x]
    print(f"{code}: 买成 {len(b)} 次 → 当日收盘相对成交 {b[:,0].mean()*100 if len(b) else float('nan'):+.2f}% / 5日 {b[:,1].mean()*100 if len(b) else float('nan'):+.2f}% ; "
          f"卖成 {len(s)} 次 → 成交相对当日收盘 {s[:,0].mean()*100 if len(s) else float('nan'):+.2f}% / 5日 {s[:,1].mean()*100 if len(s) else float('nan'):+.2f}% ; "
          f"多日轮回完成 {len(comp)}/{len(rt)}，均 {np.mean([c[0] for c in comp]) if comp else float('nan'):.1f} 天、毛利 {np.mean([c[1] for c in comp])*100 if comp else float('nan'):+.2f}%")
