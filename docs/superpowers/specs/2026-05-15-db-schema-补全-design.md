# 数据库结构补全设计 — Migration 0014

**日期：** 2026-05-15  
**状态：** 已确认，待实施

---

## 背景与动机

当前数据库模型存在若干结构性不足，若等到数据量增大后再补，将面临复杂的数据迁移甚至重建表的风险。本次统一补全，全部改动对存量数据无损（加列、改 nullable、加新表）。

触发这次设计的新需求：
- 飞书通知增强（@成员、展示发布人）
- 一键多平台分发
- 定时发布
- 文章标签分类管理
- 一键单机模式（只看自己的内容）
- 发布内容审计快照

---

## 改动总览

7 项改动，全部纳入单个 migration `0014`，revision=`"0014"`，down_revision=`"0013"`。

---

## 详细设计

### 1. `publish_tasks.platform_id` → nullable

**问题：** 现在是 NOT NULL 外键，一个任务只能绑定一个平台。跨平台任务（同时发布到头条+搜狐）创建时会被数据库拒绝。

**变更：**
```sql
-- SQLite 用 batch_alter_table 实现
ALTER TABLE publish_tasks MODIFY platform_id INTEGER NULL REFERENCES platforms(id)
```

**行为规则：**
- 历史单平台任务：`platform_id` 保持有值，行为不变
- 新跨平台任务：`platform_id = NULL`，实际平台从 `PublishTaskAccount → Account.platform_id` 推导
- 前端创建跨平台任务时不传 `platform_id`

**影响范围：** 需检查所有读取 `task.platform_id` 的代码，对 None 值做兼容处理。

---

### 2. `publish_tasks.scheduled_at` — 定时发布

**变更：**
```sql
ALTER TABLE publish_tasks ADD COLUMN scheduled_at DATETIME NULL;
```

**字段语义：** `NULL` = 立即执行；有值 = 到期后由调度器自动触发。

**调度器（本次不实现）：** 此字段先加，调度逻辑在后续功能迭代中实现。现阶段即使字段有值，任务仍由手动触发。

**模型变更：**
```python
scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

---

### 3. `users.solo_mode` — 一键单机

**变更：**
```sql
ALTER TABLE users ADD COLUMN solo_mode BOOLEAN NOT NULL DEFAULT FALSE;
```

**字段语义：**
- `False`（默认）：用户可见所有人的文章、任务（共享池模式）
- `True`：用户只看自己创建的文章和自己创建的任务

**查询层规则（实现时补充）：**
- 文章列表：`if user.solo_mode → WHERE articles.user_id = current_user.id`
- 任务列表：`if user.solo_mode → WHERE publish_tasks.user_id = current_user.id`

**模型变更：**
```python
solo_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

---

### 4. `publish_records` 内容快照

**问题：** `PublishRecord` 只保存 `article_id` 外键，文章事后被编辑后，无法知道当时实际发出去的是哪个版本。

**变更：**
```sql
ALTER TABLE publish_records ADD COLUMN snapshot_title VARCHAR(300) NULL;
ALTER TABLE publish_records ADD COLUMN snapshot_content_json TEXT NULL;
```

**写入时机：** 在 `tasks.py` 的发布流程启动时（记录状态变为 `running` 之前），将 `article.title` 和 `article.content_json` 写入快照字段，之后不再修改。

**字段约束：** 应用层保证"一旦写入就只读"，数据库层不加 trigger（保持简单）。

**模型变更：**
```python
snapshot_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
snapshot_content_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

---

### 5. 新表 `tags` — 文章标签

**设计决策：** 标签为**全局共享**（无 user_id），与平台"共享池"默认模式一致。后续若需隔离，加 user_id 列是无损迁移。

```sql
CREATE TABLE tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        VARCHAR(80) NOT NULL UNIQUE,
    created_at  DATETIME NOT NULL
);
CREATE INDEX ix_tags_name ON tags(name);
```

**模型：**
```python
class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
```

---

### 6. 新表 `article_tags` — 文章-标签 M2M

```sql
CREATE TABLE article_tags (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (article_id, tag_id)
);
CREATE INDEX ix_article_tags_tag_id ON article_tags(tag_id);
```

**模型：**
```python
class ArticleTag(Base):
    __tablename__ = "article_tags"
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
```

**Article 模型关联：**
```python
tags: Mapped[list[Tag]] = relationship("Tag", secondary="article_tags", lazy="selectin")
```

---

## 文件变更清单

| 文件 | 操作 |
|------|------|
| `server/app/models/user.py` | 加 `solo_mode` 字段 |
| `server/app/models/publish.py` | `platform_id` 改 nullable；加 `scheduled_at`；加 `snapshot_title`、`snapshot_content_json` |
| `server/app/models/article.py` | 加 `tags` relationship |
| `server/app/models/tag.py` | 新建（Tag + ArticleTag 模型） |
| `server/app/models/__init__.py` | 导出 Tag、ArticleTag |
| `server/alembic/versions/0014_db_schema_补全.py` | 新建 migration |

---

## 不在本次范围内

- 调度器实现（scheduled_at 字段先加，调度逻辑后续迭代）
- Tag CRUD API
- 前端 UI 适配
- solo_mode 的查询层过滤逻辑
- 快照写入的业务逻辑（tasks.py 修改）
- 跨平台任务前端入口

---

## 验证方式

```bash
# 1. 运行迁移
alembic upgrade head

# 2. 验证 schema（检查新列是否存在）
alembic current  # 应显示 0014

# 3. 运行全套测试
pytest server/tests/ -v

# 4. 手动检查关键表结构（SQLite）
# PRAGMA table_info(publish_tasks);   → 应有 scheduled_at, platform_id nullable
# PRAGMA table_info(publish_records); → 应有 snapshot_title, snapshot_content_json
# PRAGMA table_info(users);           → 应有 solo_mode
# SELECT * FROM tags;                 → 空表，结构正确
# SELECT * FROM article_tags;         → 空表，结构正确
```
