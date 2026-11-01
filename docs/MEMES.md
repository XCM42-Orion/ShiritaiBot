# 表情管理器

表情管理器让 Bot 在 QQ 会话里发表情包。代码在 `src/shiritaibot/meme/`，由三部分组成，
外加三个模型工具和 WebUI 的“表情管理”页。配置在 `config.toml` 的 `[meme]`；所用模型在
`routes.meme`，和其他路由放在一起。

```
群聊表情 ──► 收录器 hooker ──► 数据核心 core ◄── meme_search / meme_view / meme_save
                                  │                     ▲
                                  ▼                     │
模型调用 ◄── 注入器：提示词 ───────────────────────────┘
模型回复 ──► 注入器：拆分 ──► outbox ──► OneBot（文本 + 表情 + 文本）
```

## 1. 数据核心（`meme/core.py`）

- 图片存为 `<home>/memes/<id>`，`id` 是图片 SHA-256 的前 16 位十六进制，内容相同只存一份。
- 元数据是 `records` 表里 kind=`meme` 的记录：`category`（分类标签）、`description`（简短文字描述）、
  `mime`、`bytes`、`source`（`group` 群聊收录 / `agent` 模型收藏 / `manual` WebUI 上传）、`origin`、`created`。
- 分类在 `meme.categories` 配置，默认 10 个：happy、sad、angry、think、surprised、shy、love、tired、smug、greet。
  名称只能是小写字母、数字和下划线，说明会同时告诉主模型和分类判断。
- 描述由 `meme` 路由里的多模态文本模型转述（`CAPTION_PROMPT`，不超过 80 字）。GIF 和动画 WebP
  先取第一帧转成 PNG 再交给模型，因为不是每家接口都接受动图；发送时仍用原图。
- 描述的向量存在 `meme_vector` 表（和记忆的 `mem_vector` 分开），由后台循环按 Embedding 路由同步；
  没配 Embedding 时检索退回关键词匹配。

## 2. 收录器（`meme/hooker.py`）

白名单群里其他人每发一条带表情包的消息，收录器以概率

    p = collect_rate × (1 − 已存数量 / capacity)

尝试收录一张，库越满越慢，满了就停。同一时间只收录一张，不阻塞消息处理。

“表情包”指 NapCat 上报的 `image` 段 `sub_type≠0`（或 `summary` 含“表情”）以及 `mface` 段；
关闭 `stickers_only` 后普通图片也参与。只从 QQ 自己的 HTTPS CDN 下载，上限 2 MB。

收录流程：

1. 转述：`meme` 路由的多模态文本模型写出描述。决策模型看不到图片，所以转述必须在判断之前。
2. 判断：把默认人格的提示词和描述交给 `meme` 路由的决策模型（Jev），一次问两个问题——
   `fit`（noul：这个人设会不会发这个表情）和 `category`（choice：属于哪个分类）。
   没有决策模型或回答无效时，改用同一路由的文本模型输出 JSON。
3. `fit` 低于 `fit_threshold` 的不收录，并在内存里记住，之后不再重复判断。

## 3. 注入器（`meme/inject.py`）

**提示词**：QQ 私聊和群聊（不含 WebUI、Proactive Loop）的系统提示词末尾加一段 `<memes>`，
说明标记格式、全部已配置分类和每条回复的上限。这段只由配置决定、不含表情库内容，
收录新表情不会改变它，提示词缓存保持命中。

**拆分**：位于发送函数 `AdapterHub.enqueue` 之后、协议端之前。回复里的

- `<meme>happy</meme>`：随机发送该分类的一张表情；
- `<meme>0123456789abcdef</meme>`（或至少 6 位的唯一前缀）：发送指定表情；

会变成单独的 outbox 消息。标记在句子中间时，一条回复拆成“文本、表情、文本”三条按序发送。
未知分类、空分类和超过 `max_per_reply` 的标记被删除，前后文本合并为一条。表情以
`base64://` 图片段（`sub_type=1`）发出；群聊上下文和 WebUI 中显示为 `[表情：描述]`。

## 4. 工具（`meme/tools.py`）

| 工具 | 权限 | 作用 |
| --- | --- | --- |
| `meme_search` | safe | 按描述语义检索（Embedding，不足时补关键词），可限定分类 |
| `meme_view` | safe | 查看表情图片 |
| `meme_save` | normal | 把当前会话收到的图片（`[图片 ref=...]`）收藏进表情库，受容量限制 |

三个工具在 Chat 模式也可用，并且对所有模型都注入，工具列表不随模型变化，以保持缓存命中。
`meme_view` 的结果只带一个标记 `[表情图片 meme=<id>]`，由请求渲染（`llm/media.py`）按当次模型处理：
支持图像输入的模型在这组工具结果之后收到一条附带图片的消息（最近 `vision.max_images` 张）；
不支持的模型看到的工具结果是一条错误，提示改用 `meme_search` 的文字描述。

## 5. WebUI

“表情管理”页：开关与收录、注入参数，分类标签编辑（随“保存配置”生效），表情库浏览
（按分类筛选、搜索、分页），上传（分类和描述可留空自动判断、自动转述），逐个修改分类与描述、
重新转述、删除。WebUI 上传不做人设判断，也不受容量限制。

接口：`GET /api/memes`、`POST /api/memes`（请求体为图片）、`GET /api/memes/{id}/image`、
`PUT /api/memes/{id}`、`POST /api/memes/{id}/describe`、`DELETE /api/memes/{id}`。
