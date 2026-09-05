"""Build reviewable aggregate evidence; never exports personal orders/deals."""
import json
from pathlib import Path
import numpy as np
from mystock.ml import evaluation as ev,config

ROOT=Path(__file__).resolve().parents[2]
def main():
 p=ROOT/'data/upgrade-output/matrix';metrics=json.loads((p/'metrics.json').read_text());preds=json.loads((p/'predictions.json').read_text());d={(r['code'],r['candidate']):r for r in metrics};codes=config.TARGETS
 lines=['# ML 升级离线实验结果','',
 '固定输入：`data/upgrade-input/ml/mystock_ml.db`；哈希见本地 `data/upgrade-output/input-hashes.json`。所有细粒度结果仅留 ignored 输出。',
 '','## 协议与边界','',
 '- 旧实验 A：基线 `446e657` 的四折 CV（尾部余数原样丢弃）、全扩展特征共同掩码、CQR off、折等权；PDD alpha=0.25/0.75，其余 0.2/0.8。输出 `exp-a-frozen.txt`；本轮仅加入路径注入和单线程，不宣称修正 CV 带来的差值是模型增益。',
 '- 修正 runner：每股最近 120 个成熟开发决策 session，20 session 重拟合；标签可用日期 ≤首个测试决策日。每折训练尾部 25% 校准（训练/校准间 1 行 gap）；样本等权，各股最终等权。分红/拆股目标日不纳入 raw 标签比较，执行账户单独处理。',
 '- E1=504/756 训练窗、60/120 校准；E2=vol20/ATR 标准化；E3=leaves7/min_child50；E5=内部四特征/完整小时摘要；E4=成熟 OOF 残差最近60滚动 q（20 个残差以前使用固定 q）。每项只改一个机制，seed=0。',
 '- 开发窗口查看 1 次；另有小时完整性协议纠错后重跑该组（第二次查看该组）。Yahoo HK 12:30 bucket 跨午休，不能误认为整根是午休。所有候选小时组在同一共同 mask 重跑 control。',
 '- 没有候选接近整体 5%/3% 门槛，因此未启动 0–4 五种子晋级复核。下述配对区间只度量时间样本波动，不能当作种子稳定性证据。没有独立 holdout 或 60-session 前向 shadow。',
 '', '## 实际开发窗口','', '|股票|as_of 起止|样本数|小时共同样本|alpha(L/H)|','|---|---|---:|---:|---|']
 for c in codes:
  r=d[c,'E0_old'];h=d[c,'E5_hourly'];lines.append(f"|{c}|{r['start']} – {r['end']}|{r['n']}|{h['n']}|{r['alphas']}|")
 lines+=['','## 候选筛选（百分比，正数为损失改善）','','同时要求上下侧：相对 E0 ≥5%、相对 naive skill ≥3%、至少4/6改善、无单股恶化>10%。覆盖/宽度另列，不用 CQR 边界计算 raw pinball。','', '|候选|侧|相对旧改善%|naive skill%|改善股数|最差单股%|配对10-session块95%区间%|判定|','|---|---|---:|---:|---:|---:|---|---|']
 for name in dict.fromkeys(r['candidate'] for r in metrics):
  if name=='E5_hourly_control':continue
  for side in ['low','high']:
   k='pinball_'+side;base='E5_hourly_control' if name=='E5_hourly' else 'E0_old'
   imp=np.array([1-d[c,name][k]/d[c,base][k] for c in codes]);sk=np.array([1-d[c,name][k]/d[c,'naive_vol'][k] for c in codes])
   series=[]
   for c in codes:
    old={r['as_of']:r for r in preds[f'{c}|{base}|0']};new=preds[f'{c}|{name}|0'];alpha=d[c,name]['alphas'][0 if side=='low' else 1];y='yl' if side=='low' else 'yh';ph='lo' if side=='low' else 'hi'
    delta=[]
    for r in new:
     o=old[r['as_of']];a=o[y]-o[ph];b=r[y]-r[ph];delta.append((max(alpha*a,(alpha-1)*a)-max(alpha*b,(alpha-1)*b))/d[c,base][k])
    series.append(delta)
   length=min(map(len,series));ci=np.array(ev.block_interval(np.mean([v[-length:] for v in series],axis=0)))*100
   eligible=imp.mean()>=.05 and sk.mean()>=.03 and (imp>0).sum()>=4 and imp.min()>=-.1
   lines.append(f"|{name}|{side}|{imp.mean()*100:.2f}|{sk.mean()*100:.2f}|{(imp>0).sum()}/6|{imp.min()*100:.2f}|[{ci[0]:.2f}, {ci[1]:.2f}]|{'候选' if eligible else '不晋级'}|")
 lines+=['','小时 skill 对 naive 的全120窗口只作参考；晋级须另外对齐小时共同掩码 naive，当前相对旧改善已不满足门槛，不据该参考值晋级。配对分块按各股 session 序号对齐并等股票平均，跨市场日期不完全相同；区间为开发诊断。',
 '', '## 各股 raw 与 CQR 指标','', '|股票|候选|raw low|raw high|CQR 覆盖|CQR 宽度%|下漏/上漏|','|---|---|---:|---:|---:|---:|---|']
 for r in metrics:
  if 'pinball_low' not in r:continue
  lines.append(f"|{r['code']}|{r['candidate']}|{r['pinball_low']:.6f}|{r['pinball_high']:.6f}|{r['coverage']:.3f}|{r['width']*100:.2f}|{r['lower_miss']:.3f}/{r['upper_miss']:.3f}|")
 lines+=['','## 固定策略和后续事项','','策略汇总在第四批离线回放后追加。E6–E8、RL/TFT/HMM、外部特征采集未运行，不属于本次必交付。生产预测器和生产策略默认均未切换。']
 (ROOT/'docs/ML_UPGRADE_EXPERIMENT_RESULTS_2026-09-04.md').write_text('\n'.join(lines)+'\n')
if __name__=='__main__':main()
