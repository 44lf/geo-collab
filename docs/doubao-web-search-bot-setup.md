# 豆包联网搜索 Bot 接入 — 运维操作手册

> 目的：给 GEO 生文用的"豆包联网"接上真实联网能力。
> 交付物：一个 `bot-xxxxxxxx` 应用 ID（外加确认联网自测通过）。拿到后交给开发接进 GEO。

## 为什么必须新建"应用/Bot"（先看懂再动手）

现在 GEO 用的豆包是**模型推理接入点**（`ep-xxxx`）——它**没有联网能力**。实测：给它传联网参数，它会直接回"我没有联网能力"。

火山引擎（火山方舟）的联网是**应用层插件**，只能挂在**应用（Bot）**上、走一个**单独的接口地址**。所以必须：建一个应用 → 给它开"联网内容"插件 → 发布拿到 `bot-` 开头的 ID。**这个 `bot-` ID 才有联网，`ep-` 没有。**

⚠️ **模型限制**：联网内容插件目前**只支持 Doubao 系列 和 DeepSeek R1 系列**模型。建应用时必须选这两类之一，别选别的。

---

## 前置准备

1. 有火山方舟账号并已实名（控制台：https://console.volcengine.com/ark）。
2. 有一个方舟 **API Key**（控制台 → 左侧「API Key 管理」，没有就新建一个，复制保存）。
3. 账户已开通豆包（Doubao）模型的使用权限。

---

## 操作步骤

> 下面菜单名以火山方舟控制台当前版本为准；若某个按钮名字对不上，按"同义词"找即可（控制台偶有改版），并对照文末官方文档链接。

### 第 1 步：创建模型推理接入点（若已有豆包接入点可跳过）

1. 控制台左侧进入 **「模型推理」**。
2. 点 **【创建推理接入点】**。
3. 填名称/描述，**接入模型选一个 Doubao 系列模型**（联网插件要求）。
4. 点 **【接入模型】** 完成。

### 第 2 步：开通"联网内容"插件

1. 控制台左侧进入 **「组件库」**（有的版本叫"插件/工具"）。
2. 找到 **「联网内容」** 插件，点**开通**。
3. 里面的 **「搜索引擎」** 档位**每月有 2 万次免费额度**，够测试和小规模用；勾上它即可。

### 第 3 步：创建应用（Bot）

1. 控制台进入 **「应用实验室」/「我的应用」**。
2. 点 **【创建应用】**。
3. 类型选 **「零代码」**，模式选 **「单聊 / 对话」**。
4. 关联第 1 步创建的**豆包推理接入点**。

### 第 4 步：给应用挂上联网插件（最关键一步）

1. 在应用配置页里找到 **插件 / 联网** 区域，挂载 **「联网内容」** 插件。
2. **勾选「搜索引擎」**。
3. 选择**内容源**和**引用条数**（默认即可，引用条数可给 3~5）。
4. 展开**高级配置**，「回答方式」建议选 **「回答参考联网内容」**（不要选"严格遵守"，那个容易拒答）。

### 第 5 步：发布，拿到 bot-id

1. 点页面**右上角「发布」**。
2. 发布后，页面**左上角会出现一串 `bot-` 开头的 ID**（例如 `bot-2026xxxxxxxxxxxx`）。
3. **复制保存这个 `bot-` ID** —— 这就是交付物。

---

## 第 6 步：交付前自测（重要，别跳）

发布后**自己先验一发联网通不通**，通了再交给开发，能省一轮来回。

在能上网的机器上跑下面命令（把 `<API_KEY>` 和 `<BOT_ID>` 换成你的）：

```bash
curl -sS https://ark.cn-beijing.volces.com/api/v3/bots/chat/completions \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<BOT_ID>",
    "stream": false,
    "messages": [
      {"role": "user", "content": "今天有什么最新新闻？请给出信息来源的网页链接。"}
    ]
  }'
```

**判断标准：**

- ✅ **通了**：返回的 JSON 里能看到 **`references`** 字段（里面是一条条带 `url`/`title` 的联网来源），正文里也有具体新闻和链接。
- ❌ **没通**：返回里**没有 `references`**、或正文说"我没有联网能力"、或报 4xx 错误 —— 回到**第 4 步**检查联网插件是不是真的勾了"搜索引擎"并**重新发布**。

> 注意接口地址是 **`/api/v3/bots/chat/completions`**（中间有 `/bots/`），不是普通的 `/api/v3/chat/completions`。这是两个不同的口，普通口没联网。

---

## 交付清单（把这几项发给开发）

- [ ] **bot-id**：`bot-________________`
- [ ] **用的豆包模型**：Doubao-________（第 1 步选的那个）
- [ ] **API Key**：沿用现有的 / 还是新给一个（说明一下）
- [ ] **自测结果**：第 6 步 curl 能看到 `references` 字段 ✅

---

## 官方文档（控制台改版时对照）

- [火山方舟 · 零代码应用操作指南](https://www.volcengine.com/docs/82379/1267885)
- [火山方舟 · 应用(bot) API](https://www.volcengine.com/docs/82379/1526787)
- [火山方舟 · Bot API V2](https://www.volcengine.com/docs/82379/1265793)
- [火山方舟 · 对话(Chat) API](https://www.volcengine.com/docs/82379/1494384)
- [火山方舟 · 兼容 OpenAI SDK](https://www.volcengine.com/docs/82379/1330626)
- [火山方舟 · Base URL 及鉴权](https://www.volcengine.com/docs/82379/1298459)
- [手把手接入火山引擎联网搜索 API（开发者社区）](https://developer.volcengine.com/articles/7527946946733211711)

> ⚠️ 火山曾发过"模型插件·联网基础版/plus版/pro版下线公告"，联网档位名称可能变化——以控制台当前实际档位为准，认准"搜索引擎/联网内容"这类能返回 `references` 的即可。

---

## 附：开发侧接入备忘（给开发看，运维可忽略）

拿到 `bot-id` 后，GEO 侧不需要换掉 litellm，可用其 OpenAI 兼容路径指到 bot 口：

- `model = "openai/<bot-id>"`
- `api_base = "https://ark.cn-beijing.volces.com/api/v3/bots"`（litellm 会自动补 `/chat/completions`）
- `api_key = <方舟 API Key>`

即在「AI 模型管理」新增一行 generation 模型：model 填 `openai/bot-xxx`、base_url 填上面的 `.../api/v3/bots`。

配套改动（否则探针/生产仍会误报"联网未生效"）：

1. `model_capabilities.py` 需要新增识别豆包 bot 的联网证据——响应的 `references` 字段（现有 `_web_search_was_used` 只认 `url_citation` / `server_tool_use`，看不到 `references`）。
2. bot 口的联网是**应用侧自动触发**，不需要再传 `web_search_options`（那个字段豆包本来就忽略）。

参考本次排查记录：豆包 `ep-` 口无联网、`web_search_options` 被 litellm/`drop_params` 静默丢弃（见团队记忆 `bug-doubao-websearch-silently-dropped`）。
