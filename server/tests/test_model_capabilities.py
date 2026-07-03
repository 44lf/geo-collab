"""ai_compose「模型能力」开关 → litellm 参数映射的单元测试（纯逻辑，无需 DB）。"""

import logging

from server.app.modules.ai_generation.model_capabilities import (
    _provider_of,
    completion_with_capabilities,
)

LOG = logging.getLogger("test")


# ── 假 litellm 响应对象 ───────────────────────────────────────────────────────
class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None, reasoning_content=None, annotations=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content
        self.annotations = annotations


class _Resp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]


def _capture(final="ok"):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return _Resp(_Msg(content=final))

    fake.calls = calls
    return fake


# ── 假 Ark Responses API 响应（litellm ResponsesAPIResponse.model_dump() 结构）───────
class _FakeResponses:
    """最小 stub：只实现 model_dump()，返回 Responses API 的 output/usage 结构。"""

    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


def _make_responses(content="", reasoning=None, citations=0, tokens=(0, 0, 0)):
    output = []
    if reasoning:
        output.append(
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": reasoning}]}
        )
    if citations:
        output.append({"type": "web_search_call", "action": {"query": "q"}})
    anns = [
        {"type": "url_citation", "url": f"http://x/{i}", "title": "t"} for i in range(citations)
    ]
    output.append(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content, "annotations": anns}],
        }
    )
    ip, op, tp = tokens
    return _FakeResponses(
        {"output": output, "usage": {"input_tokens": ip, "output_tokens": op, "total_tokens": tp}}
    )


def test_provider_detection():
    assert _provider_of("moonshot/kimi-k2.5") == "moonshot"
    assert _provider_of("kimi-thinking") == "moonshot"
    assert _provider_of("anthropic/claude-opus-4-8") == "anthropic"
    assert _provider_of("openai/gpt-4o") == "openai"
    assert _provider_of("gpt-4o") == "openai"
    assert _provider_of("gemini/gemini-2.0") == "gemini"
    assert _provider_of("doubao-pro-32k") == "doubao"
    assert _provider_of("volcengine/doubao-seed-1-6") == "doubao"
    assert _provider_of("deepseek/deepseek-v4-flash") == "other"


def test_deep_thinking_injects_reasoning_effort_and_drops_anthropic_temperature():
    fake = _capture()
    base = {
        "model": "anthropic/claude-opus-4-8",
        "messages": [{"role": "user", "content": "q"}],
        "temperature": 0,
    }
    completion_with_capabilities(
        completion=fake,
        base_kwargs=base,
        model="anthropic/claude-opus-4-8",
        web_search=False,
        deep_thinking=True,
        logger=LOG,
    )
    kw = fake.calls[0]
    assert kw["reasoning_effort"] == "high"
    assert kw["drop_params"] is True
    assert "temperature" not in kw  # Anthropic 扩展思考与 temperature=0 冲突 → 去掉


def test_deep_thinking_keeps_temperature_for_non_anthropic():
    fake = _capture()
    base = {"model": "openai/gpt-4o", "messages": [], "temperature": 0}
    completion_with_capabilities(
        completion=fake,
        base_kwargs=base,
        model="openai/gpt-4o",
        web_search=False,
        deep_thinking=True,
        logger=LOG,
    )
    assert fake.calls[0]["reasoning_effort"] == "high"
    assert fake.calls[0]["temperature"] == 0


def test_web_search_native_for_anthropic_openai():
    for model in ("anthropic/claude-opus-4-8", "openai/gpt-4o"):
        fake = _capture()
        completion_with_capabilities(
            completion=fake,
            base_kwargs={"model": model, "messages": []},
            model=model,
            web_search=True,
            deep_thinking=False,
            logger=LOG,
        )
        assert fake.calls[0]["web_search_options"] == {}


def _result_line(caplog):
    """取「[生文能力·结果]」那条结果日志的文本（无则 None）。"""
    for r in caplog.records:
        if "[生文能力·结果]" in r.getMessage():
            return r.getMessage()
    return None


def test_deep_thinking_used_logged_when_response_has_reasoning(caplog):
    """深度思考开启且模型真返回思考内容 → 结果行标「深度思考=已使用」。"""

    def fake(**kwargs):
        return _Resp(_Msg(content="ok", reasoning_content="先想了想这个问题……"))

    with caplog.at_level(logging.INFO):
        completion_with_capabilities(
            completion=fake,
            base_kwargs={"model": "deepseek/deepseek-reasoner", "messages": []},
            model="deepseek/deepseek-reasoner",
            web_search=False,
            deep_thinking=True,
            logger=logging.getLogger("test.dt"),
        )
    line = _result_line(caplog)
    assert line is not None and "深度思考=已使用" in line


def test_deep_thinking_not_used_logged_when_no_reasoning(caplog):
    """深度思考开启但模型不返回思考内容 → 结果行标「深度思考=未使用」。"""
    fake = _capture()
    with caplog.at_level(logging.INFO):
        completion_with_capabilities(
            completion=fake,
            base_kwargs={"model": "deepseek/deepseek-v4-flash", "messages": []},
            model="deepseek/deepseek-v4-flash",
            web_search=False,
            deep_thinking=True,
            logger=logging.getLogger("test.dt"),
        )
    line = _result_line(caplog)
    assert line is not None and "深度思考=未使用" in line


def test_capability_result_marks_not_requested_when_disabled(caplog):
    """两个能力都不影响：未开启的能力在结果行标「未请求」，不会误报已/未使用。"""
    fake = _capture()
    with caplog.at_level(logging.INFO):
        completion_with_capabilities(
            completion=fake,
            base_kwargs={"model": "deepseek/deepseek-v4-flash", "messages": []},
            model="deepseek/deepseek-v4-flash",
            web_search=False,
            deep_thinking=False,
            logger=logging.getLogger("test.dt"),
        )
    # 两者全关 → 直接原样调用，不产生能力结果行
    assert _result_line(caplog) is None


def test_doubao_result_logs_web_search_enabled(caplog, monkeypatch):
    """开关关（=0）时豆包仍走旧的 web_search_options → 结果行标「联网搜索=已启用（web_search_options）…」。
    （开关默认开时豆包改走 Responses API，见 test_doubao_web_search_routes_to_responses_api。）"""
    monkeypatch.setenv("GEO_DOUBAO_WEB_SEARCH_VIA_RESPONSES", "0")
    fake = _capture()
    with caplog.at_level(logging.INFO):
        completion_with_capabilities(
            completion=fake,
            base_kwargs={"model": "doubao-pro-32k", "messages": []},
            model="doubao-pro-32k",
            web_search=True,
            deep_thinking=True,
            logger=logging.getLogger("test.dt"),
        )
    line = _result_line(caplog)
    assert line is not None
    assert "联网搜索=已启用" in line
    assert "web_search_options" in line  # 走原生联网选项
    assert "深度思考=" in line  # 深度思考的使用与否也一并标注


def test_native_web_search_used_when_response_has_citations(caplog):
    """原生联网且响应带 url_citation 注解 → 结果行标「联网搜索=已使用」。"""

    def fake(**kwargs):
        return _Resp(
            _Msg(
                content="ok",
                annotations=[{"type": "url_citation", "url_citation": {"url": "https://x"}}],
            )
        )

    with caplog.at_level(logging.INFO):
        completion_with_capabilities(
            completion=fake,
            base_kwargs={"model": "openai/gpt-4o", "messages": []},
            model="openai/gpt-4o",
            web_search=True,
            deep_thinking=False,
            logger=logging.getLogger("test.dt"),
        )
    line = _result_line(caplog)
    assert line is not None and "联网搜索=已使用" in line


def test_anthropic_relay_skips_native_web_search():
    """方案2：Anthropic 经中转网关（api_base 非官方）时主动跳过服务端联网，
    不再发一次注定被 web_search_20250305 name 校验拒绝的请求。深度思考仍生效。"""
    fake = _capture()
    completion_with_capabilities(
        completion=fake,
        base_kwargs={
            "model": "anthropic/claude-opus-4-8",
            "messages": [],
            "api_base": "https://relay.example.com/v1",
        },
        model="anthropic/claude-opus-4-8",
        web_search=True,
        deep_thinking=True,
        logger=LOG,
    )
    assert len(fake.calls) == 1  # 直接走基线，无「先失败再重试」的第二次调用
    kw = fake.calls[0]
    assert "web_search_options" not in kw  # 联网被跳过
    assert kw["reasoning_effort"] == "high"  # 深度思考仍生效
    assert kw["drop_params"] is True


def test_anthropic_official_still_tries_native_web_search():
    """官方直连（未配 api_base）不算中转，仍按原逻辑尝试服务端联网。"""
    fake = _capture()
    completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "anthropic/claude-opus-4-8", "messages": []},
        model="anthropic/claude-opus-4-8",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    assert fake.calls[0]["web_search_options"] == {}


def test_doubao_web_search_uses_native_options(monkeypatch):
    """开关关（=0）时豆包联网仍走旧的原生 web_search_options（回归护栏）。
    默认开时改走 Responses API，见 test_doubao_web_search_routes_to_responses_api。"""
    monkeypatch.setenv("GEO_DOUBAO_WEB_SEARCH_VIA_RESPONSES", "0")
    fake = _capture()
    completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "doubao-pro-32k", "messages": []},
        model="doubao-pro-32k",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    kw = fake.calls[0]
    assert kw["web_search_options"] == {}  # 豆包走原生联网选项
    assert "tools" not in kw  # 非 moonshot，不走 $web_search 工具循环
    assert kw["drop_params"] is True


def test_web_search_noop_for_unsupported_provider():
    fake = _capture()
    completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "deepseek/deepseek-v4-flash", "messages": []},
        model="deepseek/deepseek-v4-flash",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    kw = fake.calls[0]
    assert "web_search_options" not in kw
    assert "tools" not in kw
    assert kw["drop_params"] is True  # 靠 drop_params 静默忽略


def test_moonshot_web_search_tool_loop():
    seq = [
        _Resp(_Msg(content=None, tool_calls=[_ToolCall("c1", "$web_search", '{"q":"x"}')])),
        _Resp(_Msg(content="final")),
    ]
    state = {"i": 0, "kwargs": []}

    def fake(**kwargs):
        state["kwargs"].append(kwargs)
        r = seq[state["i"]]
        state["i"] += 1
        return r

    resp = completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "moonshot/kimi-k2.5", "messages": [{"role": "user", "content": "q"}]},
        model="moonshot/kimi-k2.5",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    assert resp.choices[0].message.content == "final"
    assert state["i"] == 2  # 一次工具调用 + 一次最终
    assert state["kwargs"][0]["tools"][0]["function"]["name"] == "$web_search"
    # 第二轮 messages 应带上 assistant(tool_calls) + tool($web_search 结果)
    msgs2 = state["kwargs"][1]["messages"]
    assert any(m.get("role") == "tool" and m.get("name") == "$web_search" for m in msgs2)


def test_native_web_search_rejection_falls_back_to_plain():
    """实测回归：某些 Anthropic 网关拒绝 litellm 的 web_search_options（工具名校验失败）→
    必须回退「能力前的原始调用」（无 web_search_options），绝不把 BadRequestError 抛给生文。"""

    def fake(**kwargs):
        if "web_search_options" in kwargs:
            raise RuntimeError("tools.0.web_search_20250305.name: Input should be 'web_search'")
        return _Resp(_Msg(content="fallback-ok"))

    resp = completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "anthropic/claude-opus-4-8", "messages": []},
        model="anthropic/claude-opus-4-8",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    assert resp.choices[0].message.content == "fallback-ok"


def test_deep_thinking_rejection_falls_back_to_plain():
    """深度思考参数被硬拒（非 drop_params 能消化的报错）→ 同样回退原始调用，不拖垮生文。"""

    def fake(**kwargs):
        if "reasoning_effort" in kwargs:
            raise RuntimeError("reasoning not supported by this endpoint")
        return _Resp(_Msg(content="plain-ok"))

    resp = completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "deepseek/deepseek-v4-flash", "messages": []},
        model="deepseek/deepseek-v4-flash",
        web_search=False,
        deep_thinking=True,
        logger=LOG,
    )
    assert resp.choices[0].message.content == "plain-ok"


def test_moonshot_web_search_failure_falls_back_to_plain():
    def fake(**kwargs):
        if "tools" in kwargs:
            raise RuntimeError("kimi search boom")
        return _Resp(_Msg(content="fallback-ok"))

    resp = completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "moonshot/kimi-k2.5", "messages": []},
        model="moonshot/kimi-k2.5",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    assert resp.choices[0].message.content == "fallback-ok"  # 联网失败 → 回退普通生文


# ── 豆包联网走 Ark Responses API（GEO_DOUBAO_WEB_SEARCH_VIA_RESPONSES 默认开）───────
def test_doubao_web_search_routes_to_responses_api(monkeypatch):
    """豆包+联网+开关默认开 → 走 litellm.responses（Ark Responses API），请求体正确、chat 不被调。"""
    import litellm

    resp_calls = []

    def fake_responses(**kwargs):
        resp_calls.append(kwargs)
        return _make_responses(content="正文", reasoning="想", citations=1, tokens=(1, 1, 2))

    monkeypatch.setattr(litellm, "responses", fake_responses)
    chat = _capture()
    completion_with_capabilities(
        completion=chat,
        base_kwargs={
            "model": "volcengine/ep-x",
            "messages": [
                {"role": "system", "content": "你是编辑"},
                {"role": "user", "content": "写新游速报"},
            ],
            "max_tokens": 1200,
            "timeout": 300,
            "api_key": "k",
        },
        model="volcengine/ep-x",
        web_search=True,
        deep_thinking=True,
        logger=LOG,
    )
    assert len(resp_calls) == 1  # 走了 Responses
    assert chat.calls == []  # 没走 chat/completions
    kw = resp_calls[0]
    assert kw["tools"] == [{"type": "web_search"}]
    assert kw["instructions"] == "你是编辑"  # system → 顶层 instructions
    assert all(m["role"] != "system" for m in kw["input"])  # input 里没有 system
    assert kw["input"][0]["content"] == "写新游速报"
    assert kw["max_output_tokens"] == 1200  # max_tokens → max_output_tokens
    assert kw["timeout"] == 300  # 超时透传
    assert kw["extra_body"]["thinking"]["type"] == "enabled"  # 深度思考走 Ark 原生 thinking


def test_doubao_responses_wrapped_as_chat(monkeypatch):
    """Responses 返回 → 包装成 chat 同构：content/reasoning/annotations/usage 全映射，检测器判定生效。"""
    import litellm

    from server.app.modules.ai_generation.model_capabilities import (
        _extract_reasoning_text,
        _web_search_was_used,
    )

    monkeypatch.setattr(
        litellm,
        "responses",
        lambda **kw: _make_responses(
            content="正文内容", reasoning="思考过程文本", citations=2, tokens=(4369, 391, 4760)
        ),
    )
    resp = completion_with_capabilities(
        completion=_capture(),
        base_kwargs={"model": "volcengine/ep-x", "messages": [{"role": "user", "content": "q"}]},
        model="volcengine/ep-x",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    msg = resp.choices[0].message
    assert msg.content == "正文内容"
    assert msg.reasoning_content == "思考过程文本"
    assert len(msg.annotations) == 2
    assert resp.usage.prompt_tokens == 4369  # input_tokens → prompt_tokens
    assert resp.usage.completion_tokens == 391  # output_tokens → completion_tokens
    assert resp.usage.total_tokens == 4760
    assert _extract_reasoning_text(resp) == "思考过程文本"
    assert _web_search_was_used(resp) is True


def test_doubao_responses_failure_falls_back_to_plain(monkeypatch):
    """Responses 调用抛异常 → best-effort 回退普通 chat/completions，不崩、不丢文章。"""
    import litellm

    attempted = []

    def boom(**kwargs):
        attempted.append(1)
        raise RuntimeError("ark boom")

    monkeypatch.setattr(litellm, "responses", boom)
    resp = completion_with_capabilities(
        completion=lambda **kw: _Resp(_Msg(content="回退正文")),
        base_kwargs={"model": "volcengine/ep-x", "messages": []},
        model="volcengine/ep-x",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    assert attempted == [1]  # Responses 确被尝试
    assert resp.choices[0].message.content == "回退正文"  # 回退到 chat


def test_doubao_no_web_search_uses_chat_not_responses(monkeypatch):
    """豆包 web_search=False → 走 chat/completions（深度思考），绝不入 Responses。"""
    import litellm

    def guard(**kwargs):
        raise AssertionError("web_search=False 不应走 Responses")

    monkeypatch.setattr(litellm, "responses", guard)
    fake = _capture()
    completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "volcengine/ep-x", "messages": []},
        model="volcengine/ep-x",
        web_search=False,
        deep_thinking=True,
        logger=LOG,
    )
    assert fake.calls[0]["reasoning_effort"] == "high"


def test_non_doubao_unaffected_by_responses_flag(monkeypatch):
    """开关默认开，但非豆包（openai）仍走 web_search_options、不碰 Responses。"""
    import litellm

    def guard(**kwargs):
        raise AssertionError("非豆包不应走 Responses")

    monkeypatch.setattr(litellm, "responses", guard)
    fake = _capture()
    completion_with_capabilities(
        completion=fake,
        base_kwargs={"model": "openai/gpt-4o", "messages": []},
        model="openai/gpt-4o",
        web_search=True,
        deep_thinking=False,
        logger=LOG,
    )
    assert fake.calls[0]["web_search_options"] == {}
