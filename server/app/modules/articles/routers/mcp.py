"""MCP-facing 文章路由（不走 user JWT，走 MCP token）。

Reason: articles_router is mounted with Depends(get_current_user) globally.
MCP service calls have no user JWT, so we expose MCP endpoints on a separate sub-router.
"""

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.articles.ai_illustrate_svc import (
    IllustrateOptions,
    illustrate_one,
)
from server.app.modules.articles.models import Article
from server.app.modules.image_library.hook import insert_images_for_article
from server.app.shared.errors import ClientError, ConflictError, ValidationError
from server.app.shared.feishu_card import build_review_link, send_review_card

# MCP 路径下没有 user JWT，跟 save_from_mcp 同款用环境变量常量
_MCP_OPERATOR_USER_ID = int(os.environ.get("GEO_MCP_OPERATOR_USER_ID", "1"))

articles_mcp_router = APIRouter()


class IllustratePayload(BaseModel):
    category_ids: list[int] | None = None  # None = use article's existing stock_categories
    image_positions: list[int] | None = None  # None = auto-detect from content


class IllustrateResponse(BaseModel):
    inserted_count: int


@articles_mcp_router.post(
    "/{article_id}/illustrate",
    response_model=IllustrateResponse,
    dependencies=[Depends(require_mcp_token)],
)
def illustrate_article_mcp(
    article_id: int,
    payload: IllustratePayload,
    db: Session = Depends(get_db),
) -> IllustrateResponse:
    """[MCP] Insert AI-selected images into the article body.

    Uses image_library/hook.py logic. POC 期：positions 默认按 content 顶层段落数自动均分。
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")

    # 选 category：payload > article.stock_categories (many-to-many relationship)
    if payload.category_ids:
        cat_ids = payload.category_ids
    else:
        cat_ids = [sc.id for sc in (article.stock_categories or [])]
    if not cat_ids:
        raise HTTPException(
            status_code=400,
            detail="no category_ids: either pass them or set article.stock_category_ids first",
        )
    category_id = cat_ids[0]

    # 自动 positions：默认在 content_json 第 2、4、6 段后插
    positions = payload.image_positions or [2, 4, 6]
    before = (
        len(article.content_json.get("content", []))
        if isinstance(article.content_json, dict)
        else 0
    )
    try:
        insert_images_for_article(article_id, category_id, positions, db)
        db.commit()
    except HTTPException:
        raise
    except (ConflictError, ClientError, ValidationError):
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise mcp_exception_response(
            exc,
            context=f"illustrate_article article_id={article_id} category_id={category_id}",
        ) from exc
    db.refresh(article)
    after = (
        len(article.content_json.get("content", []))
        if isinstance(article.content_json, dict)
        else 0
    )
    return IllustrateResponse(inserted_count=max(0, after - before))


class AiIllustratePayload(BaseModel):
    """走 ai_illustrate 节点同款逻辑（AI 决策 + 自动封面）."""

    main_category_id: int
    include_companion: bool = True
    web_fallback: bool = False
    aggressive_images: bool = True
    max_images: int | None = Field(default=None, ge=1, le=50)
    min_spacing: int | None = Field(default=None, ge=1, le=20)
    preset_id: int | None = None
    set_cover: bool = True
    # 配图模型（litellm 模型串，scope=ai_format）；None/"" = 默认格式模型
    format_engine: str | None = None
    # 上游识别分支产出的显式游戏清单 [{"game": str, "category_id"?: int, "index"?: int}]；
    # None=走现有模型识别路径。给了则走确定性落图、按游戏名匹配 heading、不调配图模型。
    game_positions: list[dict] | None = None


class AiIllustrateResponse(BaseModel):
    images_inserted: int
    cover_status: str
    cover_error: str | None
    format_error: str | None
    # warning: 0 张图但非 error（AI 决策 / 候选无图等合法分支）。MCP loop writer
    # 必须把这里非空时也当作 illustration_warnings 上报，否则文章会无图入库且无人感知。
    warning: str | None = None
    # 部分配图诊断：requested=应配位置数，missed=没配上张数，missed_games=没配上的游戏名。
    # inserted < requested（missed>0）= "该 N 张只来 M 张"，warning 会带 partial_images 文案。
    requested: int = 0
    missed: int = 0
    missed_games: list[str] = Field(default_factory=list)
    fallback_inserted: int = 0


@articles_mcp_router.post(
    "/{article_id}/ai-illustrate",
    response_model=AiIllustrateResponse,
    dependencies=[Depends(require_mcp_token)],
)
def ai_illustrate_article_mcp(
    article_id: int,
    payload: AiIllustratePayload,
) -> AiIllustrateResponse:
    """[MCP] AI 智能配图 + 自动封面，对齐 Web UI「AI 配图」pipeline 节点.

    复用 articles.ai_illustrate_svc.illustrate_one；与 pipeline 节点共享同一份实现.
    illustrate_one 内部对 run_ai_format / set_random_cover 都做了 best-effort
    包装，但上游 LiteLLM / httpx SDK 偶尔会上抛未捕获异常；用
    mcp_exception_response 兜底，避免被 main.py 全局 500 handler 抹平消息.
    """
    from server.app.db.session import SessionLocal

    try:
        result = illustrate_one(
            article_id=article_id,
            main_category_id=payload.main_category_id,
            user_id=_MCP_OPERATOR_USER_ID,
            options=IllustrateOptions(
                include_companion=payload.include_companion,
                web_fallback=payload.web_fallback,
                aggressive_images=payload.aggressive_images,
                max_images=payload.max_images,
                min_spacing=payload.min_spacing,
                preset_id=payload.preset_id,
                set_cover=payload.set_cover,
                format_model=payload.format_engine,
                game_list=payload.game_positions,
            ),
            session_factory=SessionLocal,
        )
    except HTTPException:
        raise
    except (ConflictError, ClientError, ValidationError):
        raise
    except Exception as exc:
        raise mcp_exception_response(
            exc,
            context=f"ai_illustrate article_id={article_id} category={payload.main_category_id}",
        ) from exc

    return AiIllustrateResponse(
        images_inserted=result.images_inserted,
        cover_status=result.cover_status,
        cover_error=result.cover_error,
        format_error=result.format_error,
        warning=result.warning,
        requested=result.requested,
        missed=result.missed,
        missed_games=result.missed_games,
        fallback_inserted=result.fallback_inserted,
    )


class SaveArticleFromMcpPayload(BaseModel):
    """主对话生成的 markdown 直接入库；不经 LiteLLM。

    Loop runner（Claude Code 主对话）自己写好 markdown 后调本端点——这是 MCP loop 的
    零配置生文路径，不需要 GEO_AI_API_KEY。
    """

    question_item_id: int
    prompt_template_id: int
    user_id: int
    title: str = Field(min_length=1, max_length=300)
    markdown_content: str = Field(min_length=1)
    model_label: str | None = Field(default=None, max_length=120)


class SaveArticleFromMcpResponse(BaseModel):
    article_id: int


@articles_mcp_router.post(
    "/save-from-mcp",
    response_model=SaveArticleFromMcpResponse,
    dependencies=[Depends(require_mcp_token)],
)
def save_article_from_mcp(
    payload: SaveArticleFromMcpPayload,
    db: Session = Depends(get_db),
) -> SaveArticleFromMcpResponse:
    """[MCP] 把 Loop runner 主对话生成的 markdown 落到 articles 表。

    流程：校验 question/template 存在 → 转 Tiptap+HTML → create_article → review_status=pending。
    不调任何 LLM——所以 GEO 这边不需要 GEO_AI_API_KEY 也能跑通整条 generation-loop。
    model_label 仅作 metadata 记录（写到 article.metrics['writer_model']），不影响行为。
    """
    import uuid

    from server.app.modules.ai_generation.converter import markdown_to_html, markdown_to_tiptap
    from server.app.modules.ai_generation.markdown_sanitizer import normalize_markdown_content
    from server.app.modules.ai_generation.models import QuestionItem
    from server.app.modules.articles.schemas import ArticleCreate
    from server.app.modules.articles.service import create_article as _create_article
    from server.app.modules.prompt_templates.service import get_prompt_template

    item = db.query(QuestionItem).filter(QuestionItem.id == payload.question_item_id).first()
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"question_item not found: id={payload.question_item_id}",
        )
    # get_prompt_template 已过滤软删（is_deleted）→ 软删模板视同不存在（404）。
    tpl = get_prompt_template(db, payload.prompt_template_id)
    if tpl is None:
        raise HTTPException(
            status_code=404,
            detail=f"prompt_template not found: id={payload.prompt_template_id}",
        )
    # 写入层兜底：被运营关闭（is_enabled=False）的模板不该再用于生文。
    # 与查询层（mcp catalog enabled_only=True）双保险，防 Loop 传陈旧/写死的关闭 id。
    if not tpl.is_enabled:
        raise HTTPException(
            status_code=400,
            detail=f"prompt_template disabled: id={payload.prompt_template_id}",
        )

    markdown_content = normalize_markdown_content(payload.markdown_content)

    article_payload = ArticleCreate(
        title=payload.title,
        content_json=markdown_to_tiptap(markdown_content),
        content_html=markdown_to_html(markdown_content),
        plain_text=markdown_content,
        word_count=len(markdown_content),
        client_request_id=str(uuid.uuid4()),
    )

    try:
        article = _create_article(db, payload.user_id, article_payload)
        article.review_status = "pending"
        # 生文溯源（与 pipeline 生文路径对齐，供内容列表 / 卡片展示「智能体 / 模板」）：
        # MCP loop 路径的「智能体」固定字样 "loop"；「模板」取所用提示词模板的权威名称
        # （服务端从 prompt_template_id 查得的 tpl.name，不信任客户端传参）。
        article.source_agent_name = "loop"
        article.source_template_name = tpl.name
        article.source_template_id = tpl.id
        # 对抗评审质量门 Task 7（spec §13.1 决策）：本期只存单题溯源，不加
        # question_item_ids 参数、不改 orchestrator——直接快照所查得的 QuestionItem。
        article.source_question_category = item.category
        article.source_question_texts = [item.question_text] if item.question_text else None
        if payload.model_label:
            existing = dict(article.metrics or {})
            existing["writer_model"] = payload.model_label
            article.metrics = existing
        db.commit()
    except HTTPException:
        raise
    except (ConflictError, ClientError, ValidationError):
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise mcp_exception_response(
            exc,
            context=(
                f"save_article_from_mcp qid={payload.question_item_id} "
                f"tpl={payload.prompt_template_id} user={payload.user_id}"
            ),
        ) from exc
    return SaveArticleFromMcpResponse(article_id=article.id)


class SetReviewStatusPayload(BaseModel):
    review_status: str  # "pending" | "approved"


class SetReviewStatusResponse(BaseModel):
    article_id: int
    review_status: str


@articles_mcp_router.post(
    "/{article_id}/set-review-status",
    response_model=SetReviewStatusResponse,
    dependencies=[Depends(require_mcp_token)],
)
def set_review_status_mcp(
    article_id: int,
    payload: SetReviewStatusPayload,
    db: Session = Depends(get_db),
) -> SetReviewStatusResponse:
    """[MCP] Switch article.review_status between pending / approved."""
    if payload.review_status not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail="invalid review_status")
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")
    article.review_status = payload.review_status
    db.commit()
    db.refresh(article)
    return SetReviewStatusResponse(article_id=article_id, review_status=article.review_status)


class ReviewCardPayload(BaseModel):
    title: str
    question: str = ""
    score: int | None = None
    decision: str | None = None


class ReviewCardResponse(BaseModel):
    sent: bool
    message_id: str | None = None


@articles_mcp_router.post(
    "/{article_id}/review-card",
    response_model=ReviewCardResponse,
    dependencies=[Depends(require_mcp_token)],
)
def post_review_card(
    article_id: int,
    payload: ReviewCardPayload,
    db: Session = Depends(get_db),
) -> ReviewCardResponse:
    """[MCP] 给一篇文章发一张飞书待审交互卡。开关关 / 无 chat_id → sent=False。"""
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")
    settings = get_settings()
    # 有 feishu_app_id → 拼飞书网页应用 AppLink（端内以网页应用身份打开、注入 h5sdk，
    # H5 免登才生效）；无则回落裸永久链接。详见 feishu_card.build_review_link。
    review_url = build_review_link(
        article_id=article_id,
        base_url=settings.feishu_public_base_url,
        app_id=settings.feishu_app_id,
    )
    try:
        mid = send_review_card(
            chat_id=settings.feishu_review_chat_id or "",
            article_id=article_id,
            title=payload.title,
            question=payload.question,
            score=payload.score,
            decision=payload.decision,
            review_url=review_url,
        )
    except Exception as exc:  # 理论上 send_review_card 已吞异常，这里兜底走 MCP 错误规约
        raise mcp_exception_response(exc, context=f"review_card article_id={article_id}") from exc
    return ReviewCardResponse(sent=mid is not None, message_id=mid)
