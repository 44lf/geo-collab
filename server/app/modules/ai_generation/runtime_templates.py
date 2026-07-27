"""共享的运行期 generation 提示词模板选择。"""

import random

from sqlalchemy.orm import Session

from server.app.modules.prompt_templates.models import PromptTemplate
from server.app.modules.prompt_templates.service import get_runtime_prompt_template


def pick_valid_template(
    db: Session,
    template_ids: list[int],
    user_id: int,
    *,
    rng: random.Random | None = None,
) -> PromptTemplate | None:
    """从按输入顺序去重后的可用 generation 模板中随机选择一个。"""
    candidates: list[PromptTemplate] = []
    for template_id in dict.fromkeys(template_ids):
        template = get_runtime_prompt_template(
            db, template_id, user_id=user_id, scope="generation"
        )
        if template is not None and template.is_enabled:
            candidates.append(template)
    if not candidates:
        return None
    return (rng or random.Random()).choice(candidates)
