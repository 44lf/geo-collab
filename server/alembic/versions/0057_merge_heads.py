"""merge_heads: 合并三个并行迁移头.

loop-skill 版本化(0056)、飞书 open_id 唯一索引(0056_feishu_open_id_unique,
原 id 与 0056 重复已重命名)、视频模块(0056_video_jobs)三个 MR 各自从 0055
分叉出迁移、先后合入 main,造成 multiple heads(alembic upgrade 直接报错)。
本迁移是空操作合并点,恢复单头链;对已应用过其中任意头的库均可安全 upgrade。

Revision ID: 0057_merge_heads
Revises: 0056, 0056_feishu_open_id_unique, 0056_video_jobs
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0057_merge_heads"
down_revision: str | Sequence[str] | None = (
    "0056",
    "0056_feishu_open_id_unique",
    "0056_video_jobs",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
