#!/usr/bin/env python
"""AI 能力探针：真实调用生文模型，验证「深度思考」与「联网搜索」是否真的生效。

为什么要它：pipeline 的 ai_compose 节点默认开 web_search / deep_thinking，但这两个能力
是否真在某个模型/中转网关上生效，光看配置看不出来——得真打一发 LLM、再从响应里找证据。
本脚本复刻 pipeline 生文内核（article_writer.generate_article_from_prompt）的真实调用路径
（同一个 completion_with_capabilities，provider 分叉 / 中转跳过 / fallback 全都经过），只把三个
外围换掉：**模板/问题写死、不落库、结果发飞书**。

判定标准与生产完全一致（直接复用 model_capabilities 里的检测 helper）：
- 深度思考：响应里有没有真的 reasoning_content / thinking_blocks。
- 联网搜索：响应里有没有 url_citation 引用注解 / server_tool_use.web_search_requests 计数。
  注意「无证据 ≠ 不支持」：模型也可能自行判定无需检索——所以下面写死的问题**刻意逼联网**
  （要时效信息 + 来源链接），把「支持却没搜」和「根本没联网」尽量区分开。

模型来源取并集（项目当前两个 AI 来源都覆盖）：
  ① DB 注册表 ai_models（scope=generation, enabled）
  ② env GEO_AI_ENGINES（settings.ai_engines）
按解析出的 (model, base_url) 去重，每个标注来源。

用法：
  本地：   python scripts/probe_capabilities.py
  服务器： docker compose exec app python scripts/probe_capabilities.py
  单模型： python scripts/probe_capabilities.py --model moonshot/kimi-k2-0711-preview
  多来源默认全测；结果推飞书（GEO_FEISHU_WEBHOOK_URL），未配则回落打印到控制台。
"""

from __future__ import annotations

import argparse
import logging

# ── 写死的提示词（对齐 pipeline 生文内核的组装，只是 template/question 不再从库里取）──────
# 模板 + 问题刻意选「需要时效信息 + 来源链接」的题，逼模型真的去联网，否则联网能力测不出结论。
PROBE_TEMPLATE = "你是游戏资讯编辑，写一篇简短的新游速报盘点。"
PROBE_QUESTION = (
    "1. 盘点 2026 年 6 月以来最新上线的 3 款热门游戏，"
    "每款给出准确的上线日期，并附上官方或权威媒体的信息来源链接。"
)

# 单模型正文在飞书里只放个截断标题作「确有产出」的凭证，正文不塞（飞书有长度限制、正文无意义）。
_TITLE_SNIPPET = 40
# 飞书单条文本的保守分片阈值（字符）。超过就把逐模型块拆成多条发。
_FEISHU_CHUNK_CHARS = 3000

logger = logging.getLogger("probe_capabilities")


def _build_user_prompt() -> str:
    """复刻 article_writer.generate_article_from_prompt 的 user_prompt 组装（写死变量版）。

    直接复用其公共的 render_question_prompt + 同款尾部指令，使这次调用的提示词与 pipeline
    真实生文尽量一致（差异只有：模板/问题是写死常量）。
    """
    from server.app.modules.ai_generation.article_writer import render_question_prompt

    return (
        render_question_prompt(PROBE_TEMPLATE, PROBE_QUESTION)
        + "\n\n请开始写作。第一行必须是 `# 标题`（井号后留一个空格）。"
        "正文写完后，**若本文是“每款游戏各占一个小标题”的盘点 / 推荐类文章**，"
        "在正文之后另起一行追加一个 json 代码块，按小标题顺序列出每个小标题对应的"
        "规范游戏中文名：\n"
        '```json\n{"games": ["原神", "明日方舟"]}\n```\n'
        '若是没有分款小标题的散文 / 综述，则追加 `{"games": []}`。'
        "该 json 块只用于自动配图、不展示给读者；除此之外不要输出任何前言、解释或额外代码块。"
    )


def collect_models(db, single_model: str | None) -> list[dict]:
    """收集待测模型，两个来源取并集、按 (model, base_url) 去重、标注来源。

    返回 [{"model": str, "api_key": str, "base_url": str|None, "labels": [str], "sources": [str]}]。
    single_model 非空时只返回它一个（走 resolve_writing_engine 的真实解析，DB 优先、回落 env）。
    """
    from server.app.core.config import get_settings, resolve_engine
    from server.app.modules.ai_models.service import list_models, resolve_writing_engine

    merged: dict[tuple[str, str | None], dict] = {}

    def _add(model: str, api_key: str, base_url: str | None, label: str, source: str) -> None:
        if not model:
            return
        key = (model, base_url)
        e = merged.setdefault(
            key,
            {"model": model, "api_key": api_key, "base_url": base_url, "labels": [], "sources": []},
        )
        # api_key 以「首个非空」为准（不同来源解析出的 key 理应一致，取到能用的即可）
        if not e["api_key"] and api_key:
            e["api_key"] = api_key
        if label and label not in e["labels"]:
            e["labels"].append(label)
        if source not in e["sources"]:
            e["sources"].append(source)

    if single_model:
        # 手动指定：走生产同款解析（先 DB 行匹配，无则回落 GEO_AI_ENGINES / 原样）
        model, api_key, base_url = resolve_writing_engine(db, single_model)
        _add(model, api_key, base_url, single_model, "手动")
        return list(merged.values())

    # 来源①：DB 注册表 ai_models（scope=generation 且启用）
    for row in list_models(db, scope="generation", enabled_only=True):
        # 注：按 row.model 走真实解析；同 model 多行只会命中首个 enabled，边角情形可忽略
        model, api_key, base_url = resolve_writing_engine(db, row.model)
        _add(model, api_key, base_url, row.label, "①DB")

    # 来源②：env GEO_AI_ENGINES（带内联 key、没进 DB 的模型只在这里能看到）
    settings = get_settings()
    for eng in settings.ai_engines:
        model, api_key, base_url = resolve_engine(eng.model)
        _add(model, api_key, base_url, eng.label, "②env")

    return list(merged.values())


_ARK_DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"


def _ark_base(base_url: str | None) -> str:
    """从引擎 base_url 推 Ark 根（含 /api/v3）；未配或不含 api/v3 就用默认北京站。"""
    if base_url and "/api/v3" in base_url:
        return base_url.split("/api/v3")[0] + "/api/v3"
    return _ARK_DEFAULT_BASE


def _parse_responses(d: dict) -> tuple[bool, int, int, str]:
    """解析 Ark Responses API 返回 → (web_used, 引用数, 思考字数, 正文文本)。

    output 是 item 列表：web_search_call=模型真发起过检索；message.content[].annotations 里的
    url_citation=真实来源引用；reasoning=思考内容。best-effort，字段缺失一律按无。
    """
    web_used = False
    citations = 0
    reasoning_chars = 0
    text = ""
    for item in d.get("output") or []:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "web_search_call":
            web_used = True
        elif t == "reasoning":
            rc = item.get("summary") or item.get("content") or ""
            reasoning_chars += len(rc) if isinstance(rc, str) else len(str(rc))
        elif t == "message":
            for c in item.get("content") or []:
                if not isinstance(c, dict):
                    continue
                text += c.get("text") or ""
                for a in c.get("annotations") or []:
                    if isinstance(a, dict) and "url_citation" in str(a.get("type", "")):
                        citations += 1
    if citations > 0:
        web_used = True
    return web_used, citations, reasoning_chars, text


def _probe_doubao_responses(entry: dict, result: dict) -> dict:
    """豆包联网必须走 Responses API + tools:[{type:web_search}]——chat/completions 的
    web_search_options 豆包根本不认（litellm 对 volcengine 不支持、被 drop_params 丢）。
    直连 httpx 打 /responses（litellm volcengine 走 chat/completions、不覆盖 Responses API）。"""
    import httpx

    from server.app.modules.ai_generation.article_writer import (
        _split_games_block,
        extract_title_and_body,
    )

    model_id = entry["model"].split("/", 1)[-1]  # 去 volcengine/ 前缀，Ark 要 bare ep-/doubao- id
    url = f"{_ark_base(entry.get('base_url'))}/responses"
    result["via"] = "Responses API + web_search 工具"
    body = {
        "model": model_id,
        "input": [{"type": "message", "role": "user", "content": _build_user_prompt()}],
        "tools": [{"type": "web_search"}],
    }
    try:
        r = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {entry['api_key']}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=300,
        )
        r.raise_for_status()
        d = r.json()
    except Exception as exc:  # noqa: BLE001 — 单模型失败不拖垮整轮
        detail = getattr(getattr(exc, "response", None), "text", "") or ""
        result["error"] = f"{type(exc).__name__}: {str(exc)[:150]} {detail[:150]}".strip()
        return result

    web_used, citations, reasoning_chars, text = _parse_responses(d)
    result["web_used"] = web_used
    result["web_citations"] = citations
    result["thinking_used"] = reasoning_chars > 0
    result["thinking_chars"] = reasoning_chars
    try:
        body_md, _ = _split_games_block(text)
        title, _ = extract_title_and_body(body_md)
        result["title"] = title[:_TITLE_SNIPPET]
    except Exception:  # noqa: BLE001
        result["title"] = "(无法解析标题)"
    return result


def probe_one(entry: dict) -> dict:
    """对单个模型真打一发 both-on 调用，返回判定结果 dict。异常收进 error、不抛。"""
    import litellm

    from server.app.modules.ai_generation.article_writer import (
        _split_games_block,
        extract_title_and_body,
    )
    from server.app.modules.ai_generation.model_capabilities import (
        _extract_reasoning_text,
        _provider_of,
        _web_search_was_used,
        completion_with_capabilities,
    )

    model = entry["model"]
    label = " / ".join(entry["labels"]) or model
    sources = "".join(entry["sources"])
    result = {"model": model, "label": label, "sources": sources, "error": None}

    if not entry["api_key"]:
        result["error"] = "缺 api_key（api_key_env 未设且无 scope 全局 key）"
        return result

    # 豆包/volcengine：联网走 Responses API 分支（与生产未来修法一致），其它模型走 litellm chat/completions
    if _provider_of(model) == "doubao":
        return _probe_doubao_responses(entry, result)

    try:
        response = completion_with_capabilities(
            completion=litellm.completion,
            base_kwargs={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是一位专业的内容写作者。根据下方的写作要求，撰写一篇高质量的文章。"
                            "使用 Markdown 格式输出正文。输出的第一行必须是文章标题，格式为 `# 标题`。"
                        ),
                    },
                    {"role": "user", "content": _build_user_prompt()},
                ],
                "api_key": entry["api_key"] or None,
                "api_base": entry["base_url"] or None,
                "timeout": 300,
                "max_tokens": 12000,
            },
            model=model,
            web_search=True,
            deep_thinking=True,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 — 单模型失败不拖垮整轮
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return result

    # 深度思考判定：响应里有没有真的思考内容
    reasoning = _extract_reasoning_text(response)
    result["thinking_used"] = bool(reasoning)
    result["thinking_chars"] = len(reasoning) if reasoning else 0

    # 联网判定：有没有 url_citation / server_tool_use 检索证据
    result["web_used"] = _web_search_was_used(response)

    # 顺手抽个标题当「确有产出」的凭证（不落库、不塞正文）
    try:
        content = response.choices[0].message.content or ""
        body, _games = _split_games_block(content)
        title, _ = extract_title_and_body(body)
        result["title"] = title[:_TITLE_SNIPPET]
    except Exception:  # noqa: BLE001 — 标题只是凭证，抽不出不影响判定
        result["title"] = "(无法解析标题)"
    return result


def format_block(r: dict) -> str:
    """把单模型结果格式化成飞书里的一个块。"""
    head = f"─ {r['model']} [{r['sources']}]"
    if r.get("error"):
        return f"{head}\n  ❌ {r['error']}"
    think = (
        f"✓ 已思考({r['thinking_chars']}字)"
        if r["thinking_used"]
        else "✗ 未返回思考内容(模型不支持或 reasoning_effort 被忽略)"
    )
    if r["web_used"]:
        cites = f"，{r['web_citations']}条引用" if r.get("web_citations") else ""
        web = f"✓ 已检索{cites}"
    else:
        web = "启用·本次无证据(不支持 / 中转网关跳过 / 模型判定无需联网)"
    via = f"\n  调用方式: {r['via']}" if r.get("via") else ""
    return f"{head}\n  深度思考: {think}\n  联网: {web}\n  产出标题: {r.get('title', '')}{via}"


def _chunk_blocks(blocks: list[str], budget: int) -> list[str]:
    """把逐模型块按字符预算合并成若干条消息文本。"""
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for b in blocks:
        if cur and cur_len + len(b) > budget:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += len(b) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def deliver(results: list[dict], dry: bool = False) -> None:
    """发飞书（未配置 webhook 则回落打印到控制台）。dry=True 时只打印、绝不发飞书。"""
    blocks = [format_block(r) for r in results]
    ok = sum(1 for r in results if not r.get("error"))
    think_ok = sum(1 for r in results if r.get("thinking_used"))
    web_ok = sum(1 for r in results if r.get("web_used"))
    summary = (
        f"共 {len(results)} 个模型 | 调用成功 {ok} | "
        f"深度思考生效 {think_ok} | 联网生效 {web_ok}"
    )

    all_sent = True
    if not dry:
        from server.app.shared.feishu import send_text

        chunks = _chunk_blocks(blocks, _FEISHU_CHUNK_CHARS)
        total = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            title = "🔎 AI能力探针" + (f"（{i}/{total}）" if total > 1 else "")
            body = (summary + "\n\n" + chunk) if i == 1 else chunk
            sent = send_text(title, body, level="info")
            all_sent = all_sent and sent

    # 无论飞书成没成，都在控制台打一份（本地跑 / webhook 未配 / --dry 的兜底）
    print("\n" + "=" * 60)
    print(summary)
    print("=" * 60)
    for b in blocks:
        print(b)
    print("=" * 60)
    if dry:
        print("（--dry：仅控制台输出，未发飞书）")
    elif not all_sent:
        print("⚠️ 飞书未配置或发送失败（GEO_FEISHU_WEBHOOK_URL），以上仅控制台输出。")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 能力探针：验证深度思考 / 联网搜索是否真生效")
    parser.add_argument(
        "--model",
        default=None,
        help="只测这一个 model 串（走 resolve_writing_engine 真实解析）；不传则测两个来源全部启用模型",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="只打印控制台、不发飞书（本地验证用）",
    )
    args = parser.parse_args()

    from server.app.core.logging import configure_logging
    from server.app.db.session import SessionLocal

    configure_logging()  # 顺带把 [生文能力·请求/结果] 日志打到控制台

    db = SessionLocal()
    try:
        models = collect_models(db, args.model)
    finally:
        db.close()

    if not models:
        logger.warning("没有可测的模型（DB 注册表无启用生文模型、GEO_AI_ENGINES 也为空）")
        deliver([])
        return

    logger.info("待测模型 %d 个：%s", len(models), [m["model"] for m in models])
    results = [probe_one(m) for m in models]
    deliver(results, dry=args.dry)


if __name__ == "__main__":
    main()
