# myStock 升级前备份记录

> 时间：2026-09-04 22:15:30 America/Los_Angeles；状态：已完成并验证。
> 基线：`3b80d12c0070a01f1019f9ae46205132b946730e`，main，备份开始时工作区干净。
> 对应执行工单：[ML_UPGRADE_WORK_ORDER_2026-09-04.md](ML_UPGRADE_WORK_ORDER_2026-09-04.md)。

## 1. 备份位置与范围

本机私有目录：`/Users/kk/Work/Workpace/GitHub/Seattle/myStock-backups/pre-upgrade-20260904-221530`。

目录位于 Git 仓库外，父目录及本次目录权限为 `0700`。备份含敏感配置与业务数据，仅留在本机，不提交 Git、不上传报告服务器。

| 文件／目录 | 内容 |
| --- | --- |
| `snapshot/` | 工作区文件快照，包括 `mystock/web`、`mystock/ml`、其他源码、scripts、tests、docs、环境定义、本地配置及完整 `data/` 文件内容 |
| `workspace.tar.gz` | 上述快照的归档，解压后顶层为 `workspace/`；6,216,254 字节 |
| `repository.bundle` | 全部 Git refs 可达历史；已通过 `git bundle verify` |
| `MANIFEST.json` | 基线、137 个文件的大小／SHA-256、数据库表行数、采集与验证记录；仅在私有备份中保存 |
| `SHA256SUMS` | 归档、bundle、manifest、环境记录及恢复说明的校验值 |
| `python-packages.json` | 本机 mk 环境的 Python 包版本，未导出环境变量 |
| `RESTORE.md` | 本地恢复说明 |

`.git` 由 bundle 承载；不复制可重建缓存 `__pycache__ / .pytest_cache` 或 `.DS_Store`。SQLite 的 WAL／SHM／journal 不作为独立恢复文件，使用 SQLite 在线备份接口合并为可独立恢复的数据库。未备份完整 conda 二进制环境、外部 OpenD 或公网服务器；环境定义和包版本已留存。

归档 SHA-256：

```text
b5e001ef6eb495bb032bc3fbea76616ecd4beeadb4e0da65d43ac411aaa51014
```

## 2. 实际验证结果

- 源库以 `mode=ro` 打开并设置 `query_only=ON`，SQLite online backup 写到新目录。
- `data/mystock.db`：10 张业务表，备份 `PRAGMA integrity_check` 返回 `ok`，表行数与源库一致。
- `data/ml/mystock_ml.db`：7 张业务表，备份 `PRAGMA integrity_check` 返回 `ok`，表行数与源库一致。
- 捕获前后源文件大小／mtime／模式清单一致；Git HEAD 和工作区状态未改变。两个库分别备份，未宣称跨库分布式原子快照。
- 非数据库文件与源文件 SHA-256 一致。
- 将归档解压到私有临时目录，重新核对全部 137 个文件 SHA-256，并对解压出的两个库重做 integrity_check，均通过；演练临时目录已移除。
- 验证未替换运行中的文件、未写源数据库、未启动训练／采集／发布、未停止现有 Web 服务。

## 3. 恢复与执行隔离

执行任务先验证 `SHA256SUMS`，再将 `snapshot/data/` **复制**到自己的隔离工作树；不能用软链接把可写路径指回备份或当前运行目录。原备份不可作为实验输出目录。

正式回滚时先停止目标实例的写入者和 Web 服务，另存升级后的现场与新增业务数据；在新目录解压、验证、启动独立端口，确认后再切换路径。禁止在 SQLite 正被使用时覆盖 db 或混用新旧 WAL。数据回滚可能丢失备份之后的新增记录，是否原地替换须在明确保留方案后单独决定。

Git 可从 bundle 克隆并回到上述基线，再恢复对应配置与数据。本轮只完成隔离解压与数据库／文件验证，没有对现有服务进行切换演练。
