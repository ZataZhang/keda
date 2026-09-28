# Alembic Migration Standards

本页定义本仓库 Alembic 迁移脚本的命名与生成约束。

## File Name Format

新生成的迁移脚本必须使用：

```text
YYYYMMDD_HHMMSS_<slug>.py
```

- 时间戳取运行命令时本地 `date +%Y%m%d_%H%M%S`，必须精确到秒。
- `slug` 使用小写蛇形命名，并描述迁移目的。
- `revision` 必须等于文件名去掉 `.py` 后的时间戳前缀。
- `down_revision` 必须指向创建时 `alembic heads` 的唯一 head。

## Required Generation Entry Point

禁止手工创建、重命名或编造迁移时间戳。必须从仓库根目录执行：

```bash
just new-migration <slug>
```

该入口调用 `scripts/shared/alembic/new_migration.sh`，会验证版本图只有一个 head、调用 Alembic 模板生成文件，并同步文件名、`revision` 与 `down_revision`。只在生成后用补丁填写 `upgrade()` 和 `downgrade()`。

交付前执行：

```bash
uv run alembic heads
```

输出必须只有一个 head，且应为新迁移的 revision。

## Backfill Idempotency

回填型迁移（upgrade 中 UPDATE/DELETE 现有数据）必须容忍重复执行。`env.py` 的 `transaction_per_migration` 已防住 MySQL DDL 隐式提交导致的 version 滞后，但 `alembic_version` 仍可能因手动 `alembic stamp` 回旧版本、备份部分恢复或 downgrade 中断而落后于实际 schema；此时重跑迁移是正常路径，回填逻辑必须安全。

### 唯一约束下的回填陷阱

单条 `UPDATE` 在有唯一键时会逐行检查约束，目标值与未更新行的当前值在中间状态构成置换就会撞键：例如某 session 当前 `sequence = [1, 0, 2]`，`ROW_NUMBER()` 目标 = `[1, 2, 3]`，把第二行 `0 -> 2` 时第三行仍是 `2`，立即冲突。MySQL 逐行立即检查；PostgreSQL 默认 IMMEDIATE 唯一约束同样逐行检查（实测 PostgreSQL 17 亦报 `UniqueViolation`）。这是方言级陷阱，看 SQL 字面逻辑无法发现。

### 推荐写法

涉及唯一约束/唯一索引列的回填，用"先移除约束 -> 回填 -> 重建约束"模式，让回填不受约束限制；DDL 后刷新 inspector 再判断是否重建：

```python
if "uq_xxx" in existing_constraint_names:
    op.drop_constraint("uq_xxx", "table_name", type_="unique")
    inspector = sa.inspect(op.get_bind())  # DDL 后刷新，确保后续 create 判断反映最新 schema
op.execute(sa.text("UPDATE ... SET col = ROW_NUMBER() ..."))
if "uq_xxx" not in refreshed_constraint_names:
    op.create_unique_constraint("uq_xxx", "table_name", [...])
```

不涉及唯一约束的回填，也要保证重跑幂等：赋值在重跑时不变，或用 `WHERE col = <default>` 只处理未回填的行。downgrade 同样要可重跑。

## Foreign Key and Index Drop Order

`downgrade()`（有时也包括 `upgrade()` 里的回滚分支）在同一张表上既要删外键约束、又要删索引或唯一约束时，两者的相对顺序**在 MySQL 上**是硬约束；这条陷阱只影响 MySQL 方言，PostgreSQL 不受影响。

### 外键约束依赖下的索引删除陷阱（仅 MySQL）

MySQL 8 InnoDB 拒绝删除仍被外键约束依赖的索引：`DROP INDEX`/`ALTER TABLE ... DROP KEY` 在该索引还支撑着一个 FOREIGN KEY 时报 1553（`Cannot drop index 'X': needed in a foreign key constraint`）。这条约束只在索引所在表本身**存活**时才会触发——如果该表随后整体 `drop_table`，DROP TABLE 是单条 DDL，会原子性地连同索引与外键约束一起清除，不受这条限制。

PostgreSQL 没有这个坑：PostgreSQL 的外键约束只要求**被引用**（父表）一侧存在唯一索引/约束，对**引用**（子表）一侧的普通索引没有目录级依赖（`pg_depend` 不会把子表索引挂在 FK 约束上），子表索引删除顺序与该表是否有外键约束无关。这条约定因此不需要、也不应该套到 PostgreSQL 分支上。

这个坑在 SQLite 上同样不可见：Alembic 的 `batch_alter_table` 在 SQLite 上走"整表重建"策略（建临时表 -> 拷贝数据 -> 删旧表 -> 改名），不会对旧表执行真正的 `DROP INDEX`。修法分两种：

- **表最终被整体 drop_table**：前面对该表索引的单独 `drop_index` 调用是多余的，直接删掉即可（索引会随 `drop_table` 一并清除）。
- **表本身存活，只做列级手术**：必须先 `drop_constraint(..., type_="foreignkey")`，再 `drop_index(...)`/`drop_constraint(..., type_="unique")`，顺序不能反。

### 自动化覆盖范围

`tests/guards/shared/test_migration_foreign_key_index_drop_order.py` 对"表存活"这一种形状做静态 AST 检查（同一个 `upgrade()`/`downgrade()` 函数内，同一张表只要同时出现显式外键约束删除与索引/唯一约束删除，就要求前者的源码行号更靠前），随模板 sync 分发、在每次改动时自动生效，且不需要连接数据库。本仓库当前**还没有任何 Alembic 迁移**，该守卫在 `alembic/versions` 找不到迁移文件时自动跳过（`pytest.skip`，非静默通过）；引入首个迁移后它才开始真正检查。

这类顺序缺陷只在真实 MySQL 上通过 `upgrade -> downgrade -> upgrade` 回环才会暴露；常规测试固定使用 SQLite（`batch_alter_table` 整表重建，看不到这个坑），因此在这条真实 MySQL 回环验证补齐之前，上面的静态检查是该形状**唯一**的自动化防线。

"索引删除后紧跟整表 drop_table"这一种形状**没有**静态检查覆盖：能否安全删除取决于被删索引的列是否恰好是某个外键约束的列，仅按"这张表在别处有没有任意外键"做表级粗判会产生真实误报（同一张表完全可能既有外键约束、又有一批与该外键无关的普通索引，这些索引被删后紧跟整表 `drop_table`，全程无 1553 风险）。要安全覆盖这一种形状，需要把外键约束与索引各自的列集合做静态交叉比对（含 `op.f()` 包装、多列索引/外键、inline `sa.ForeignKey`/`ForeignKeyConstraint` 等写法），复杂度与误判面显著高于收益，因此暂不做。
