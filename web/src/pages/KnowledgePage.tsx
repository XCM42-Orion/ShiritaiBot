import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Search, Trash2, Upload } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { KnowledgeDocument, KnowledgeHit } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { FieldForm } from "../components/FieldForm";
import { Pagination } from "../components/Pagination";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { KNOWLEDGE_FIELDS } from "../config/schema";
import type { Action, ConfigPageProps } from "./common";

const PAGE_SIZE = 20;
const CHUNK_PAGE = 20;
const ACCEPT =
  ".txt,.md,.markdown,.rst,.log,.json,.yaml,.yml,.xml,.csv,.tsv,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx";
const SOURCES: Record<string, string> = {
  web: "WebUI 上传",
  qq: "QQ 私聊",
  agent: "助手写入",
};
const MATCHES: Record<KnowledgeHit["match"], string> = {
  hybrid: "关键词 + 语义",
  keyword: "关键词",
  semantic: "语义",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function KnowledgePage({
  client,
  act,
  config,
  setConfig,
}: ConfigPageProps) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const listing = useQuery({
    queryKey: ["knowledge", query, page],
    queryFn: () => client.knowledge(query, page, PAGE_SIZE),
    refetchInterval: 15000,
  });
  const data = listing.data;

  return (
    <>
      <section className="panel">
        <h2>知识库</h2>
        <p className="muted">
          知识库存放不会变化的资料：手册、规章、表格、讲义。它和长期记忆不同，不区分人物和事件，也不追踪时效，检索不调用文本模型，适合存放大量文档。在
          QQ 私聊里把文件发给助手并说“存到知识库”，或在下方上传。语义检索使用
          Embedding 路由，没有配置时只用关键词。
        </p>
        <FieldForm
          value={config.knowledge}
          fields={KNOWLEDGE_FIELDS}
          onChange={(next) => setConfig({ ...config, knowledge: next })}
        />
      </section>
      <SearchPanel client={client} />
      <section className="panel">
        <h2>文档</h2>
        {data && (
          <p className="muted">
            {data.documents} 篇 · {data.chunks} 个分块 ·{" "}
            {formatBytes(data.bytes)} · 向量{" "}
            {data.index.model
              ? `${data.index.indexed} 已索引，${data.index.pending} 待计算`
              : "未配置 Embedding，检索只用关键词"}
          </p>
        )}
        <UploadForm
          client={client}
          act={act}
          limit={config.knowledge.max_file_mb}
        />
        <form
          className="inline"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(draft.trim());
            setPage(1);
          }}
        >
          <input
            aria-label="筛选文档"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="标题或文件名"
          />
          <button type="submit">
            <Search size={16} />
            筛选
          </button>
        </form>
        {listing.error && (
          <QueryErrorNotice
            message={`读取知识库失败：${listing.error.message}`}
          />
        )}
        {data && !data.items.length ? (
          <EmptyState
            text={
              query
                ? "没有符合条件的文档"
                : "知识库还是空的。上传文件，或在 QQ 私聊里发给助手。"
            }
          />
        ) : (
          <div className="kb-documents">
            {data?.items.map((row) => (
              <DocumentRow key={row.id} client={client} act={act} row={row} />
            ))}
          </div>
        )}
        {data && (
          <Pagination
            label="文档分页"
            page={data.page}
            total={data.total}
            pageSize={PAGE_SIZE}
            onChange={setPage}
            loading={listing.isFetching}
          />
        )}
      </section>
    </>
  );
}

function UploadForm({
  client,
  act,
  limit,
}: {
  client: ApiClient;
  act: Action;
  limit: number;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [reset, setReset] = useState(0);
  return (
    <form
      className="kb-upload"
      onSubmit={(event) => {
        event.preventDefault();
        if (!files.length) return;
        void act(async () => {
          // One at a time so that a bad file names itself and the rest stay queued.
          for (const [index, file] of files.entries()) {
            try {
              await client.uploadKnowledge(file);
            } catch (error) {
              setFiles(files.slice(index));
              throw new Error(
                `${file.name}：${error instanceof Error ? error.message : "上传失败"}`,
                { cause: error },
              );
            }
          }
          setFiles([]);
          setReset((value) => value + 1);
        }, "文档已加入知识库");
      }}
    >
      <label className="field">
        <span className="field-name">
          上传文件（每个 {limit} MiB 以内，可多选）
        </span>
        <input
          key={reset}
          aria-label="知识库文件"
          type="file"
          multiple
          accept={ACCEPT}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
        <small className="muted">
          支持 txt、md、pdf、doc(x)、csv、xls(x)、ppt(x) 等文本格式；扫描版 PDF
          需要先做 OCR。
        </small>
      </label>
      <button type="submit" className="primary" disabled={!files.length}>
        <Upload size={16} />
        上传{files.length > 1 ? ` ${files.length} 个文件` : ""}
      </button>
    </form>
  );
}

function SearchPanel({ client }: { client: ApiClient }) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const hits = useQuery({
    queryKey: ["knowledge-search", query],
    queryFn: () => client.knowledgeSearch(query),
    enabled: Boolean(query),
  });
  return (
    <section className="panel">
      <h2>检索测试</h2>
      <p className="muted">结果和助手调用 kb_search 时看到的相同。</p>
      <form
        className="inline"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <input
          aria-label="检索知识库"
          value={draft}
          maxLength={500}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="输入一个问题或关键词"
        />
        <button type="submit" disabled={!draft.trim()}>
          <Search size={16} />
          检索
        </button>
      </form>
      {hits.error && (
        <QueryErrorNotice message={`检索失败：${hits.error.message}`} />
      )}
      {hits.data && !hits.data.length && <EmptyState text="没有检索到内容" />}
      {hits.data?.map((hit) => (
        <article className="kb-chunk" key={`${hit.document}#${hit.seq}`}>
          <div className="kb-chunk-head">
            <strong>{hit.title}</strong>
            {hit.label && <span className="muted">{hit.label}</span>}
            <code>
              {hit.document}#{hit.seq}
            </code>
            <span className="tag">{MATCHES[hit.match]}</span>
          </div>
          <p>{hit.text}</p>
        </article>
      ))}
    </section>
  );
}

function DocumentRow({
  client,
  act,
  row,
}: {
  client: ApiClient;
  act: Action;
  row: KnowledgeDocument;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  return (
    <article className="kb-document">
      <div className="kb-document-head">
        <div className="kb-document-title">
          <strong>{row.title}</strong>
          <span className="muted">
            <code>{row.id}</code> · {row.filename} · {formatBytes(row.bytes)} ·{" "}
            {row.chunks} 个分块 · {SOURCES[row.source] ?? row.source} ·{" "}
            {new Date(row.created * 1000).toLocaleDateString()}
          </span>
        </div>
        <div className="actions">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "收起" : "查看分块"}
          </button>
          <button
            type="button"
            aria-label={`下载 ${row.title}`}
            onClick={() =>
              void act(async () => {
                const url = URL.createObjectURL(
                  await client.knowledgeFile(row.id),
                );
                const link = document.createElement("a");
                link.href = url;
                link.download = row.filename;
                link.click();
                URL.revokeObjectURL(url);
              }, "")
            }
          >
            <Download size={16} />
          </button>
          {confirming ? (
            <>
              <button
                type="button"
                className="danger"
                onClick={() =>
                  void act(() => client.deleteKnowledge(row.id), "文档已删除")
                }
              >
                确认删除
              </button>
              <button type="button" onClick={() => setConfirming(false)}>
                取消
              </button>
            </>
          ) : (
            <button
              type="button"
              className="danger"
              aria-label={`删除 ${row.title}`}
              onClick={() => setConfirming(true)}
            >
              <Trash2 size={16} />
            </button>
          )}
        </div>
      </div>
      {open && <ChunkList client={client} id={row.id} total={row.chunks} />}
    </article>
  );
}

function ChunkList({
  client,
  id,
  total,
}: {
  client: ApiClient;
  id: string;
  total: number;
}) {
  const [page, setPage] = useState(1);
  const detail = useQuery({
    queryKey: ["knowledge-document", id, page],
    queryFn: () =>
      client.knowledgeDocument(id, (page - 1) * CHUNK_PAGE, CHUNK_PAGE),
  });
  return (
    <div className="kb-chunks">
      {detail.error && (
        <QueryErrorNotice message={`读取分块失败：${detail.error.message}`} />
      )}
      {detail.data?.items.map((chunk) => (
        <article className="kb-chunk" key={chunk.seq}>
          <div className="kb-chunk-head">
            <code>#{chunk.seq}</code>
            {chunk.label && <span className="muted">{chunk.label}</span>}
          </div>
          <p>{chunk.text}</p>
        </article>
      ))}
      <Pagination
        label={`文档 ${id} 分块分页`}
        page={page}
        total={total}
        pageSize={CHUNK_PAGE}
        onChange={setPage}
        loading={detail.isFetching}
      />
    </div>
  );
}
