# 知识库

知识库存放不会变化的资料：手册、规章、表格、讲义。代码在 `src/shiritaibot/knowledge/`，配置在
`config.toml` 的 `[knowledge]`，WebUI 对应“知识库”页。

## 1. 和记忆层的区别

| | 长期记忆（`docs/MEMORY.md`） | 知识库 |
| --- | --- | --- |
| 内容 | 人物、事物、事件，会变化 | 固定的文档，不会变化 |
| 结构 | 实体、事实、事件、证据、冲突 | 文档 → 分块，只有两层 |
| 写入 | 模型抽取，代码校验证据 | 导入文件，不调用文本模型 |
| 修改 | 取代、合并、标记冲突 | 只能整篇删除后重新导入 |
| 使用 | 每轮按需注入 | 模型调用 `kb_search` 时才检索，不注入 |

## 2. 存储

- 原始文件存为 `<home>/knowledge/<sha256>`，内容相同只存一份。
- `kb_document` 是文档元数据，对外编号 `d12`；`kb_chunk` 是分块，对外写作 `d12#3`（第 3 块，从 0 起）。
- `kb_fts` 是 FTS5 全文索引，`rowid` 等于分块 id；`kb_vector` 是向量，和记忆、表情分开。
- 一篇文档的所有行在一个事务里写入，中途失败不会留下半篇文档。

## 3. 导入

支持的格式和切分方式：

| 格式 | 解析 | 分节依据 |
| --- | --- | --- |
| txt、rst、log、json、yaml、yml、xml | UTF-8 / UTF-16 / GB18030 自动识别 | 不分节 |
| md、markdown | 同上 | 标题路径（`甲 > 乙`） |
| csv、tsv | 自动识别分隔符 | 表格 |
| pdf | pypdf | 每页一节；扫描版没有文本层，会报错提示先做 OCR |
| docx | python-docx | 标题路径，正文和表格保持原有顺序 |
| doc | olefile 读取 Word 二进制格式的 piece table | 不分节 |
| xlsx / xls | openpyxl / xlrd | 每个工作表一节，按表格处理 |
| pptx | python-pptx | 每张幻灯片一节，含备注 |
| ppt | olefile 读取 PowerPoint 记录 | 每张幻灯片一节，含备注 |

- 解析方式由文件头决定：扩展名写 `.doc` 而内容其实是 OOXML 也能读。RTF 和加密文件会明确报错。
- `.ppt` 沿 Current User → UserEdit → PersistDirectory 找到最新版本。快速保存会把旧版本留在文件里，
  顺序扫描会读到旧文本和错误页码。这条链损坏时才退回扫描全部文本，此时不再分页。
- `.doc` 只取正文，不含页眉页脚和脚注。

分块：

- 正文按行累积到 `chunk_chars`，单行过长时先按句子切，再硬切；相邻分块在同一节内重叠 `chunk_overlap` 字符。
- 表格按行切分，每块重复表头，不重叠，这样单元格脱离上下文后含义仍然清楚。
- 改分块参数只影响之后导入的文档。

## 4. 检索

`kb_search` 把两路结果做倒数排名融合（RRF，k=60），每路最多取 50 条：

- **关键词**：FTS5 BM25。索引和查询都同时使用 jieba 分词和中文二元组：jieba 会切开词典外的词（“年假”→“年”“假”），
  而且同一个词在问题和文档里可能切法不同，二元组保证两边总能对上。
- **语义**：`embedding` 路由的向量，余弦相似度低于 0.3 的丢弃。

分块的标题和分节名称一起进入索引。结果带 `match` 字段：`hybrid`、`keyword` 或 `semantic`。
全程不调用文本模型。

向量由后台循环补齐：导入后立即唤醒，只计算当前模型缺失的向量；更换 Embedding 模型后会全部重算。
没有配置 Embedding 或调用失败时只用关键词，导入不受影响。

向量检索是 numpy 暴力计算，全部向量常驻内存：10 万个 1024 维分块约占 400 MB。规模再大需要换近似索引。

## 5. 工具

| 工具 | 权限 | Chat 模式 | 作用 |
| --- | --- | --- | --- |
| `kb_search` | safe | 看 `knowledge.chat` | 混合检索，可限定文档 |
| `kb_read` | safe | 看 `knowledge.chat` | 按顺序读取分块，用来看命中处的上下文 |
| `kb_list` | safe | 看 `knowledge.chat` | 列出文档 |
| `kb_import` | normal | 不开放 | 导入 QQ 文件引用、会话目录里的文件或纯文本 |
| `kb_delete` | sensitive | 不开放 | 删除一篇文档 |

`kb_import` 和 `kb_delete` 在代码里再次检查，只有主人的私聊可以调用。`kb_import` 的 `path` 只能指向当前会话的
工作目录或下载目录，所以它不能用来读取任意文件。

QQ 私聊的流程：主人发送文件并说“存到知识库”，消息里带 `[QQ 文件引用 ref: 文件名]`，模型把 ref 传给 `kb_import`。
不会自动入库，只发文件不说明用途时由模型询问或判断。

开启 `knowledge.chat` 后，其他人的私聊和群聊也能让助手读到知识库的全部内容，没有按文档控制权限。

## 6. 配置

```toml
[knowledge]
enabled = true       # 关闭只隐藏工具，已存文档不受影响
chat = false         # 是否在 Chat 模式开放只读工具
chunk_chars = 600    # 200–2000
chunk_overlap = 80   # 0–500，不超过 chunk_chars 的一半
max_file_mb = 50     # 1–50
```

## 7. WebUI 接口

`GET /api/knowledge`、`POST /api/knowledge?filename=&title=`（请求体为文件）、`GET /api/knowledge/search?q=`、
`GET /api/knowledge/{id}?start=&count=`、`GET /api/knowledge/{id}/file`、`DELETE /api/knowledge/{id}`。
