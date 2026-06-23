"""myStock ML 子包 —— 训练/回测专用，与 web 只读生产库完全解耦。

设计原则（见 docs/ML_PLAN.md）：
  - 训练数据写独立库 data/ml/mystock_ml.db（不碰 data/mystock.db）。
  - 扩充数据采集独立于 scripts/update.sh。
  - 仅只读生产库做交易事实快照（deals/orders/positions）。
  - 第一版标的：US.NVDA / US.TSLA / US.PDD（单标的独立资金）。
"""
