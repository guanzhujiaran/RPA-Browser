# RPA Alembic 版本合并（单条基线重建）计划书（v1）

> 状态：**已实施**（2026-10-21）
> 结果：`alembic/versions` 只剩 `20261021_rpa_baseline.py` 一条；空库 `upgrade head` 建出 28 张表，
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

## 5. 验收标准

- [x] `ls alembic/versions` 只剩 1 个文件（`20261021_rpa_baseline.py`）；
- [x] `alembic upgrade head` 在空库成功，`alembic current` 显示新基线；
- [x] 新旧两库结构 diff「实质差异（表/列/类型/可空性/索引/表选项）」为空；
- [x] `downgrade base` 后库内无残留表，`upgrade head` 可再次建出；
- [x] `check_schemas()`（启动期校验）通过。

## 6. 已知非实质差异（可接受）

| 差异 | 说明 |
|---|---|
| 列物理顺序 | 旧链把增量列追加在末尾，新基线按模型声明顺序建列；语义等价 |
| `compositeactionmodel.action_type` 枚举标签 | 新基线按当前模型补齐了 `SHUFFLE_LIST` / `SORT_LIST` / `BILI_RESERVE` / `PICK_LINE`（旧链缺失这些标签，属旧链落后于模型） |
| 部分列的 `COMMENT` | 旧链只在「手写增量脚本」里给 20 来个列写了注释；模型元数据不含注释，基线（autogenerate 产物）因此不带；不影响功能 |
| 部分列的 `DEFAULT` | 同上：那些 `DEFAULT 0/1/''` 来自手写增量脚本，模型侧是 Python 默认值（非 server_default）。应用全走 ORM 插入（仓库内无裸 SQL INSERT），行为不变 |
