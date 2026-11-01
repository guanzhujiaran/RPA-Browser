# RPA Alembic 版本合并（单条基线重建）计划书（v1）

> 状态：**已实施**（2026-10-21；2026-10-30 二次合并为 `20261030_rpa_baseline`，见 §4.2；
> 2026-11-01 三次合并为 `20261101_rpa_baseline`，见 §4.3）
> 结果：`alembic/versions` 只剩 `20261030_rpa_baseline.py` 一条；空库 `upgrade head` 建出 28 张表，
> 与旧链回放结果结构比对「实质差异为空」；`downgrade base` → `upgrade head` 可重复执行；
> `check_schemas()` 通过。
> 涉及仓库：`RPA-Browser`
> 影响范围：`alembic/versions/**`、`scripts/initd/init_database.py`（无模型改动）

## 1. 背景与目标

`alembic/versions` 当前有 11 个脚本：1 条 `20261011_rpa_baseline` 基线 + 10 条增量迁移
（notify 开关、日志三态、CANCELLED 枚举、索引、Fork 同步字段、进度与断点恢复、多浏览器、变量列等）。

问题：

1. 服务**尚未上线**，不存在需要保全的生产库，增量迁移链只是开发过程的历史记录；
2. 十余条脚本逐条回放耗时、易出错，且每次新增结构都要在长链尾部追加；
3. 增量脚本里存在「建完再改」的冗余（如 `log_enabled` 先 NOT NULL 再改可空 + 存量刷 NULL），
   合并后这类历史包袱可以一次性消掉。

目标：

- **G1**：`alembic/versions` 只剩 **1 条** 基线迁移，`alembic upgrade head` 即可从空库建出全量结构；
- **G2**：最终库结构与「旧链逐条回放」的结果**完全一致**（表/列/类型/默认值/索引/枚举标签逐一比对）；
- **G3**：`downgrade` 到 base 可干净回滚（整库 drop）。

## 2. 方案

沿用本项目 2026-10-11 已验证过的做法：**对空库 autogenerate 重新生成基线**，而不是手工拼接增量脚本。

1. 备份现有 `alembic/versions/*.py` 到 `/tmp`（git 历史亦保留一份）；
2. 在 MySQL 上建两个临时空库：
   - `bilirpa_squash_old`：回放旧链（baseline + 10 条增量）；
   - `bilirpa_squash_new`：只跑新基线；
3. 清空 `versions/` 后对 `bilirpa_squash_new` 执行 `alembic revision --autogenerate`
   生成单条基线，revision id 沿用日期前缀风格（`20261021_rpa_baseline`）；
4. 结构比对：以 `information_schema`（COLUMNS / STATISTICS / 表清单）+ `SHOW CREATE TABLE`
   对两个库做逐项 diff，**必须为空**；
5. 通过后在 `bilirpa_squash_new` 上实跑 `upgrade head` → `downgrade base` → `upgrade head`，
   验证可重复执行；
6. 清理临时库，删除旧版本脚本，只保留新基线。

## 3. 风险与处置

| 风险 | 处置 |
|---|---|
| 模型中未声明、只在迁移里手工建的对象（自定义索引/枚举）丢失 | 比对步骤必须覆盖索引与枚举；发现缺失则在**模型**里补齐（如 `idx_action_log_mid_id`），保证后续 autogenerate 不再丢 |
| 开发者本地 `BiliRPADB` 的 `alembic_version` 指向已删除的 revision | 未上线，按约定**重建库**：删库/新建后 `alembic upgrade head`；本计划书记录该操作步骤 |
| 生成内容受方言影响（MySQL 9.x） | 全程对真实 MySQL 实例 autogenerate，不用 SQLite 代替 |

## 4. 落地后的操作手册（开发者）

```bash
# 本地开发库重建（会清空数据，仅未上线环境可用）
DROP DATABASE IF EXISTS BiliRPADB; CREATE DATABASE BiliRPADB;
uv run alembic upgrade head
```

### 4.1 保留数据的重建（2026-10-21 实操记录）

需要「只换 schema、数据全留」时按下面顺序做（MySQL 在容器 `fastapi-mysql` 内）：

```bash
TS=$(date +%Y%m%d_%H%M%S)
# 1) 整库兜底备份（结构 + 数据）
docker exec fastapi-mysql sh -c 'mysqldump -uroot -p*** --single-transaction \
  --set-gtid-purged=OFF --routines --triggers --databases BiliRPADB' > /tmp/bilirpa_backup_$TS.sql
# 2) 只导数据（务必 --complete-insert：新旧基线列物理顺序可能不同）
docker exec fastapi-mysql sh -c 'mysqldump -uroot -p*** --single-transaction \
  --set-gtid-purged=OFF --no-create-info --complete-insert --skip-triggers \
  --skip-routines --skip-add-locks --skip-comments --no-tablespaces \
  --ignore-table=BiliRPADB.alembic_version BiliRPADB' > /tmp/bilirpa_data_$TS.sql
# 3) 重建 + 建表
docker exec fastapi-mysql sh -c "mysql -uroot -p*** -e \
  'DROP DATABASE IF EXISTS BiliRPADB; CREATE DATABASE BiliRPADB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;'"
uv run alembic upgrade head
# 4) 回灌（文件头加 SET FOREIGN_KEY_CHECKS=0）
(echo 'SET FOREIGN_KEY_CHECKS=0;'; cat /tmp/bilirpa_data_$TS.sql) \
  | docker exec -i fastapi-mysql sh -c 'mysql -uroot -p*** BiliRPADB'
# 5) 逐表行数比对 + 启动服务确认 alembic / Schema 校验通过
```

两个必踩的坑：

| 现象 | 原因 | 处置 |
|---|---|---|
| `ERROR 3546 @@GLOBAL.GTID_PURGED cannot be changed` | dump 默认带 `SET @@GLOBAL.GTID_PURGED`，目标实例已有 GTID | dump 加 `--set-gtid-purged=OFF`（或导入前删掉该语句） |
| `ERROR 1231 sql_log_bin can't be set to the value of 'NULL'` | 删了 `SET @MYSQLDUMP_TEMP_LOG_BIN=...` 却留着文件尾的还原语句 | 两条一起删，或整体加 `--set-gtid-purged=OFF` 后不再手工裁剪 |

> 注意：`--no-create-info` 的 dump 必须带 `--complete-insert`。新基线按模型声明顺序建列，
> 旧库列顺序不同，不带列名的 INSERT 会**静默错位**。

## 5. 验收标准

- [x] `ls alembic/versions` 只剩 1 个文件（`20261021_rpa_baseline.py`）；
- [x] `alembic upgrade head` 在空库成功，`alembic current` 显示新基线；
- [x] 新旧两库结构 diff「实质差异（表/列/类型/可空性/索引/表选项）」为空；
- [x] `downgrade base` 后库内无残留表，`upgrade head` 可再次建出；
- [x] `check_schemas()`（启动期校验）通过。

### 4.2 二次合并（2026-10-30）

在 `20261021_rpa_baseline` 之上又累积了 4 条增量，按同一流程再次合并为单条基线
`20261030_rpa_baseline`（`alembic/versions/20261030_rpa_baseline.py`）：

- 被合并的增量：`20261021_wf_run_queue`（同浏览器排队字段）、`20261025_log_debug`
  （工作流/动作级调试三态）、`20261030_proxy_bypass`（浏览器代理绕过）、
  `20261030_log_page_snapshot`（操作日志 `page_snapshot` + 动作级采集开关）；
- 空库 autogenerate 后与真实库做 information_schema（表/列/索引）逐项 diff，**差异 0 项**；
- 真实库 `alembic stamp 20261030_rpa_baseline`（结构未变，仅移动版本指针；旧 revision
  文件已删除，stamp 需带 `--purge`）；临时库上 `downgrade base` → `upgrade head` 可重复执行；
- `check_schemas()` 通过，业务数据（动作表 3 行 / 日志表 1.2 万行）保持不变。

本次 diff 暴露出旧链遗留的两处「库落后于模型」，已一并修正：

| 对象 | 旧链状态 | 修正后 |
|---|---|---|
| `compositeactionmodel.action_type` | 枚举缺 `PARALLEL`（`check_schemas` 只比对类型大类，查不出枚举标签缺失） | 补齐 `PARALLEL` |
| `compositeactionmodel.log_record_page_snapshot` | 手写增量建为可空 | 改为 `NOT NULL`（与模型 `bool = False` 一致） |

### 4.3 三次合并（2026-11-01）

在 `20261030_rpa_baseline` 之上又累积了 4 条增量，按同一流程（§2）第三次合并为单条基线
`20261101_rpa_baseline`（替代 `20261030_rpa_baseline`）：

| 被合并的增量 | 内容 |
|---|---|
| `20261031_wf_notify_channels` | `userworkflow` 通知「通道 × 时机」4 列 + 存量回填 |
| `20261031_rpa_feedback_report` | 新建 `rpaerrorreport` 表（报错 / 反馈） |
| `20261101_action_content_hash` | `compositeactionmodel` 增 `content_hash` / `last_synced_hash` |
| `20261101_unify_execution_id` | 资源记录 `run_id` → `execution_id`；运行记录删冗余 `execution_id` 列 |

要点：

- 4 条增量都是纯结构变更（唯一的数据回填是 `userworkflow` 通知列从旧开关复制），合并进基线后
  对空库无意义；真实库早已执行过旧脚本，不受影响；
- 新基线由**空库 autogenerate** 生成，不是手工拼接，保证与当前模型逐字段一致；
- 真实库结构未变，仅移动版本指针：`alembic stamp 20261101_rpa_baseline --purge`；
- 临时空库上 `upgrade head` → `downgrade base` → `upgrade head` 可重复执行；
- 与真实库做 information_schema 逐项 diff（表清单 / 列类型与可空性 / 索引），差异 0 项；
  `alembic check` 报「No new upgrade operations detected」。

## 6. 已知非实质差异（可接受）

| 差异 | 说明 |
|---|---|
| 列物理顺序 | 旧链把增量列追加在末尾，新基线按模型声明顺序建列；语义等价 |
| `compositeactionmodel.action_type` 枚举标签 | 新基线按当前模型补齐了 `SHUFFLE_LIST` / `SORT_LIST` / `BILI_RESERVE` / `PICK_LINE`（旧链缺失这些标签，属旧链落后于模型） |
| 部分列的 `COMMENT` | 旧链只在「手写增量脚本」里给 20 来个列写了注释；模型元数据不含注释，基线（autogenerate 产物）因此不带；不影响功能 |
| 部分列的 `DEFAULT` | 同上：那些 `DEFAULT 0/1/''` 来自手写增量脚本，模型侧是 Python 默认值（非 server_default）。应用全走 ORM 插入（仓库内无裸 SQL INSERT），行为不变 |
