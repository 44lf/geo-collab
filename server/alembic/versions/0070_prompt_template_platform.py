"""prompt_templates.platform"""

import sqlalchemy as sa

from alembic import op

revision: str = "0070_prompt_template_platform"
down_revision: str | None = "0069_game_cull_and_manual"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("prompt_templates", sa.Column("platform", sa.String(50), nullable=True))
    op.create_index("ix_prompt_templates_platform", "prompt_templates", ["platform"])


def downgrade() -> None:
    op.drop_index("ix_prompt_templates_platform", table_name="prompt_templates")
    op.drop_column("prompt_templates", "platform")
