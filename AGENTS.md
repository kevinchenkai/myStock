# myStock 项目约定

Codex 与 Claude 共用此文件；只保留长期有效的规则，运行细节见 [README](README.md)，文档入口见 [docs/README.md](docs/README.md)。

## 目标与边界

- 本地港股／美股数据与复盘系统。ML 预测下一交易日低价／高价，辅助人类低买高卖；以扣除成本后的周期收益与风险评估效果，不能只看价格误差或命中率。
- Web 只读 SQLite，不抓取、不训练、不写库；采集和训练走独立管线。依赖只能单向：Web 可只读 ML（顶层 import，缺 pandas/numpy 会导致 Web 起不来，不能声称全是延迟导入），ML 不得 import Web。
- 历史补齐、重算与当时生成的 live 预测必须区分；预测版本追加留档，不覆盖审计证据。模型评估避免未来信息泄漏，工程验收通过不代表模型收益提升。
- 金额注明币种，跨币种不能直接相加；涨跌红涨绿跌。具体数据口径按 README 与数据字典执行。

## 工作方式

- 开始先看 `git status`，保留已有改动；按任务查阅相关代码、现行文档与 [未尽事项](docs/OPEN_ITEMS.md)，历史方案不自动视为现行要求。
- 按用户已授权的范围完成工作，不重复索要已给出的授权。修改运行库、重启服务、公网发布、调度或真实下单须在授权范围内；迁移／破坏性数据变更前先备份并确认恢复路径。
- 密钥、真实账户数据、`config.yaml`、`config.*.local.yaml`、`data/`、`*.db*` 不得提交或公开；实验使用隔离数据副本。
- 验证与改动匹配：Python 测试用 conda `mk`；完整 ML 测试在隔离副本运行；JS 改动用 `node --check`；文档改动运行 `python3 scripts/check_docs.py`，提交前运行 `git diff --check`。
- 交付说明改了什么、验证结果和未解决事项；后续问题登记到 `docs/OPEN_ITEMS.md`，不虚报执行、测试或收益。声明「未做某项高风险动作」前须确认属实——错误的声明比不声明更糟。

## 协作与维护

- 用 `docs/` + Git 交接，不读取另一 agent 的会话、进程或工作树来推测进度；具体流程见 [协作约定](docs/COLLABORATION.md)。小改动用提交说明即可，不为流程额外堆文档。
- 文档按 [治理规范](docs/GOVERNANCE.md) 使用 `主题_原始作者_YYYYMMDD`，同步索引、清单与引用；根目录 `AGENTS.md`、`CLAUDE.md` 保留固定名称。
- 共用规则只改本文件；Claude 专属补充才写 `CLAUDE.md`。命令、模块清单、阶段状态和实验结论写 README／docs，避免重复维护。
- Codex 创建的每个 Git 提交须保留用户配置的 author／committer，并附且仅附一次：`Co-authored-by: Codex <codex@openai.com>`。其他工具按实际作者署名。
