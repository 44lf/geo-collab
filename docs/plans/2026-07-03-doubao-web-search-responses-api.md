# 豆包生文联网改走 Responses API（定稿 · 已双路调研）

> 背景：豆包联网在 chat/completions + `web_search_options` 下空转（litellm 对 volcengine 不支持该参数、被 drop_params 丢）。实测 Ark Responses API + `tools:[{type:web_search}]` 在现有 ep- 接入点直接通。原型见根目录 `probe_capabilities.py:_probe_doubao_responses`；团队记忆 `bug-doubao-websearch-silently-dropped`。
> 本稿已由两个 subagent 调研落定所有假设（内部契约/测试/配置 + 外部 Ark 契约/litellm 支持度）。

## 目标

pipeline / 方案运行里走豆包（volcengine/doubao）的生文，`web_search=True` 时真联网检索。**只动豆包联网这一条分支**；其它 provider、深度思考、非联网路径零改动。

## 调研落定的关键事实

- **用 `litellm.responses()`，不写 raw httpx**：litellm `1.86.0`（`requirements.txt:20` 已 pin）原生支持 volcengine Responses（`VolcEngineResponsesAPIConfig`，`utils.py:8666` 路由）。端到端实测通过。加一条「litellm≥1.86.0」守卫注释防降级。
- **模型门槛已满足**：现有 `ep-m-20260604143113-rg29v` 背后是 `doubao-seed-2-0-pro-260215`，web_search + thinking 实测均生效。
- **契约面极窄**：`completion_with_capabilities` 生产唯一消费者是 `article_writer.py:207`，返回对象不透传下游。唯一功能硬依赖是 `.choices[0].message.content`；`usage/reasoning/annotations` 均 None-safe、缺了只让 `[生文能力·结果]` 日志失真、不崩。

## 改动点（唯一文件）

`server/app/modules/ai_generation/model_capabilities.py` 的 `completion_with_capabilities()`：doubao 现在命中 `_supports_native_web_search_options` → 发 `web_search_options={}`（空转）。改成 doubao + web_search（且开关开）时走 Responses 分支。

### 设计

1. **`completion_with_capabilities` 的签名 / 返回契约不变**（明确要求：函数名、参数、返回字段都不动，调用方 `article_writer.py:207` 零改动）。豆包分支在函数**内部** lazy `import litellm` 调 `litellm.responses`（仅 doubao+web_search 时触发，其它路径不 import）。测试注入点用 monkeypatch 模块内私有函数（见「测试」节），不靠新增形参。

2. **新增 `_doubao_web_search_completion(*, responses_call, base_kwargs, model, deep_thinking, logger)`**：
   - 拆 messages：`role=="system"` 的 join 进顶层 `instructions`；其余 → `input=[{"type":"message","role","content"}]`。
   - 组 kwargs：`model`（**保留 `volcengine/` 前缀**，litellm 路由要）、`input`、`instructions`、`tools=[{"type":"web_search"}]`、`max_output_tokens=base_kwargs["max_tokens"]`、透传 `api_key/api_base/timeout`；`deep_thinking` → `extra_body={"thinking":{"type":"enabled"}}`（Ark 原生思考，**不是** `reasoning:{effort}`）。
   - `resp = responses_call(**kwargs)` → `return _wrap_responses_as_chat(resp)`。

3. **新增 `_wrap_responses_as_chat(resp) -> ChatLike`**：`resp.model_dump()` 转 dict 后按下表解析（复用 `probe_capabilities.py:_parse_responses` 的解析思路），产出暴露以下字段的轻量对象（SimpleNamespace/小 dataclass 即可）：

   | chat 同构字段 | Responses 源 |
   |---|---|
   | `.choices[0].message.content` | `output[]` 中 `type=="message"` → `content[]` 的 `output_text.text` 拼接（或 `resp.output_text` 便捷属性） |
   | `.choices[0].message.reasoning_content` | `output[]` 中 `type=="reasoning"` → `summary[]` 的 `summary_text.text` 拼接 |
   | `.choices[0].message.annotations` | 同 message 的 `content[].annotations`（`type=="url_citation"`：url/title/site_name/publish_time/summary） |
   | `.usage.prompt_tokens` | `usage.input_tokens` |
   | `.usage.completion_tokens` | `usage.output_tokens` |
   | `.usage.total_tokens` | `usage.total_tokens` |

   联网证据**统一映射进 `annotations`**（`_web_search_was_used` 先走 annotations 分支即 True，不填 `usage.server_tool_use`，None-safe 不影响）。

4. **路由 + 开关**：在 `completion_with_capabilities` 里，`_provider_of(model)=="doubao" and web_search and _via_responses_enabled()` → 调 `_doubao_web_search_completion`；**沿用现有 best-effort 降级**（Responses 异常 → 去联网、保留深度思考回退普通 chat/completions，语义不变）。`web_search=False` 的豆包**不动**（仍 chat/completions + `reasoning_effort`，深度思考已证真生效）。

### 配置开关（照 `GEO_TOUTIAO_DRIVER` 的 os.environ 直读范式）

`GEO_TOUTIAO_DRIVER` 不在 config.py，而在 `drivers/__init__.py:100` 直读 `os.environ`。照此：在 `model_capabilities.py` 加 `import os` + 布尔 helper：

```python
def _via_responses_enabled() -> bool:
    # 默认开（已实测生效）；一键回滚：GEO_DOUBAO_WEB_SEARCH_VIA_RESPONSES=0
    return os.environ.get("GEO_DOUBAO_WEB_SEARCH_VIA_RESPONSES", "1").strip().lower() not in ("0", "false", "no", "off")
```

保持该模块不 import config 的解耦现状；测试用 `monkeypatch.setenv/delenv`（无需 `cache_clear`）。

## 测试（TDD，先红后绿）

新增（`test_model_capabilities.py`；不改 `completion_with_capabilities` 签名，用 monkeypatch 注入）：
1. **路由 + 请求体**：`monkeypatch.setattr(litellm, "responses", fake)` 捕获 kwargs；doubao + web_search + flag 开 → 断言：`input` 无 system、`instructions` 有 system 文本、`tools==[{"type":"web_search"}]`、`extra_body.thinking.type=="enabled"`、`max_output_tokens` 来自 max_tokens、**`timeout` 透传自 base_kwargs（=300）**。路由/包装/回退三类也可改 monkeypatch 模块级 `_doubao_web_search_completion`（更轻，不碰 litellm）。
2. **包装**：喂假 `ResponsesAPIResponse`（含 message/reasoning/web_search_call + url_citation annotations + input/output/total_tokens）→ 断言包装对象 `.choices[0].message.content/reasoning_content/annotations` 与 `.usage.prompt/completion/total_tokens` 全部正确映射；`_extract_reasoning_text` / `_web_search_was_used` 对它判定为「已思考/已检索」。
3. **回退**：`responses_call` 抛异常 → 走现有 fallback、不抛、返回普通 chat 结果、生文不中断。
4. **开关关**：doubao + web_search + flag 关 → 仍走 `web_search_options`（旧行为）。
5. **非联网豆包**：doubao + `web_search=False` → 走 chat/completions，不入 Responses。
6. **非 doubao 回归**：openai/anthropic/moonshot 完全走旧路径、零变化。

**须同步更新的既有用例**（否则 CI 硬门禁变红）：`test_doubao_web_search_uses_native_options`、`test_doubao_result_logs_web_search_enabled` 当前断言豆包走 `web_search_options={}`——改成「flag 关时仍走旧路径」或替换为断言新 Responses 行为。

## 非目标

- 不改深度思考路径（已真生效）。不改其它 provider 联网。不做 Bot 接入点、不接 Brave（均已排除）。
- 不动 `probe_capabilities.py`（原型已在）。生产落定后可回头把探针的 doubao 分支也切到 `litellm.responses` 对齐，非本次范围。

## 并发 / 超时 / 延迟

- **不新增并发**：豆包分支是在现有生文工作线程里的同步调用（一次 `litellm.responses` 换掉一次 chat/completions），**不 spawn 新线程**。并发上限沿用现有几道闸：`ai_compose`/scheme 的 `ThreadPoolExecutor(max_workers=4)` + pipeline 全局闸 `GEO_PIPELINE_MAX_CONCURRENT_RUNS`（默认 3）+ 同 pipeline 行锁。同时在飞的豆包 Responses 调用 ≈ 4（单次运行内）。故**无需新增并发控制**。
- **超时**：透传现有 `base_kwargs["timeout"]=300`（5min）给 `litellm.responses`（timeout 是 litellm 标准 kwarg）。实测豆包联网单篇 ~40–52s，300s 兜得住。**测试项①断言 timeout 已透传**，不靠假设。
- **限流：不预加 throttle，靠 fallback 兜底**。Ark web_search 的 QPS 上限未实测（区别于配图那条千帆搜图有实测 ~3-4/s 硬顶、专门加了 throttle）；豆包联网并发被生文 max_workers=4 天然压得很低，先不做过度设计。万一撞 Ark 限流/超时，异常走**现有 best-effort 回退**（去联网、回退普通生文），不崩、不丢文章、只是那篇没联网。等生产观察到限流再按需加 throttle（同当初千帆的处置顺序）。
- **延迟上升（预期代价，非 bug）**：联网多轮检索使单篇从 ~15s 涨到 ~40–50s，4 并发下整批吞吐下降——**开了联网的豆包 pipeline 会明显变慢**，运营需知情。

## 风险 / 回滚

- 开关**默认开**（`GEO_DOUBAO_WEB_SEARCH_VIA_RESPONSES` 缺省=开，已实测生效）；`=0` 秒回滚到旧行为。
- Responses 调用失败自动 best-effort 回退普通生文，不影响出文。
- litellm 降级到 <1.86.0 会失去 volcengine Responses 支持 → 加守卫注释；`requirements.txt` 已 pin 1.86.0。
- 成本：web_search 计费；`tools` 可选 `max_keyword` 控每轮关键词数，本次先用默认 `{type:web_search}`，成本敏感时再调。
