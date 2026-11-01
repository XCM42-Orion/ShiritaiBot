import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Search, Trash2, Upload } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { MemeCategory, MemeRecord } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { FieldForm } from "../components/FieldForm";
import { Pagination } from "../components/Pagination";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { MEME_FIELDS } from "../config/schema";
import type { Action, ConfigPageProps } from "./common";

const PAGE_SIZE = 24;
const CATEGORY_NAME = /^[a-z][a-z0-9_]{0,31}$/;
const SOURCES: Record<string, string> = {
  group: "群聊收录",
  agent: "模型收藏",
  manual: "手动上传",
};

export function MemesPage({ client, act, config, setConfig }: ConfigPageProps) {
  const meme = config.meme;
  const [category, setCategory] = useState("");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const listing = useQuery({
    queryKey: ["memes", category, query, page],
    queryFn: () => client.memes(category, query, page, PAGE_SIZE),
    refetchInterval: 15000,
  });
  const counts = listing.data?.counts ?? {};

  function setCategories(categories: MemeCategory[]) {
    setConfig({ ...config, meme: { ...meme, categories } });
  }

  return (
    <>
      <section className="panel">
        <h2>表情管理器</h2>
        <p className="muted">
          表情管理器由三部分组成：表情库按分类存储表情和文字描述；收录器从白名单群聊收集其他人发的表情包；注入器告诉模型如何用
          &lt;meme&gt;分类&lt;/meme&gt;
          发表情，并在发送前把标记换成表情消息。转述与分类所用的模型在“模型与服务
          → 路由 → 表情转述与分类模型”中配置，语义检索使用 Embedding 路由。
        </p>
        <FieldForm
          value={meme}
          fields={MEME_FIELDS}
          onChange={(next) => setConfig({ ...config, meme: next })}
        />
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>分类标签</h2>
          <button
            type="button"
            disabled={meme.categories.length >= 30}
            onClick={() =>
              setCategories([
                ...meme.categories,
                { name: uniqueName(meme.categories), description: "" },
              ])
            }
          >
            <Plus size={16} />
            添加分类
          </button>
        </div>
        <p className="muted">
          名称用于 &lt;meme&gt;名称&lt;/meme&gt;
          标记，只能使用小写字母、数字和下划线，以字母开头；说明告诉模型和分类判断这个分类表达什么。改名或删除分类后，原分类下的表情需要重新归类才能被随机发送。
        </p>
        <div className="meme-categories">
          {meme.categories.map((item, index) => {
            const invalid = !CATEGORY_NAME.test(item.name);
            const duplicate = meme.categories.some(
              (other, position) =>
                position !== index && other.name === item.name,
            );
            return (
              <div className="meme-category" key={index}>
                <input
                  aria-label={`分类 ${index + 1} 名称`}
                  aria-invalid={invalid || duplicate}
                  value={item.name}
                  onChange={(event) =>
                    setCategories(
                      meme.categories.map((row, position) =>
                        position === index
                          ? { ...row, name: event.target.value.trim() }
                          : row,
                      ),
                    )
                  }
                />
                <input
                  aria-label={`分类 ${index + 1} 说明`}
                  value={item.description}
                  maxLength={200}
                  placeholder="这个分类表达的情绪或用途"
                  onChange={(event) =>
                    setCategories(
                      meme.categories.map((row, position) =>
                        position === index
                          ? { ...row, description: event.target.value }
                          : row,
                      ),
                    )
                  }
                />
                <span className="tag">{counts[item.name] ?? 0} 个</span>
                <button
                  type="button"
                  className="danger"
                  aria-label={`删除分类 ${item.name}`}
                  disabled={meme.categories.length <= 1}
                  onClick={() =>
                    setCategories(
                      meme.categories.filter(
                        (_, position) => position !== index,
                      ),
                    )
                  }
                >
                  <Trash2 size={16} />
                </button>
                {(invalid || duplicate) && (
                  <small role="alert">
                    {invalid ? "名称格式无效" : "分类名称重复"}
                  </small>
                )}
              </div>
            );
          })}
        </div>
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>表情库</h2>
          {listing.data && (
            <span className="tag">
              {listing.data.count} / {listing.data.capacity} 个 · 向量{" "}
              {listing.data.index.model
                ? `${listing.data.index.indexed} 已索引，${listing.data.index.pending} 待计算`
                : "未配置 Embedding，检索只用关键词"}
            </span>
          )}
        </div>
        <UploadForm client={client} act={act} categories={meme.categories} />
        <div className="range-tabs" role="group" aria-label="表情分类">
          {[{ name: "", description: "" }, ...meme.categories].map((item) => (
            <button
              type="button"
              key={item.name || "all"}
              className={category === item.name ? "selected" : ""}
              aria-pressed={category === item.name}
              onClick={() => {
                setCategory(item.name);
                setPage(1);
              }}
            >
              {item.name || "全部"}
              {item.name && ` (${counts[item.name] ?? 0})`}
            </button>
          ))}
        </div>
        <form
          className="inline"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(draft.trim());
            setPage(1);
          }}
        >
          <input
            aria-label="搜索表情"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="描述中的关键词或表情 ID"
          />
          <button type="submit">
            <Search size={16} />
            搜索
          </button>
        </form>
        {listing.error && (
          <QueryErrorNotice
            message={`读取表情失败：${listing.error.message}`}
          />
        )}
        {listing.data && !listing.data.items.length ? (
          <EmptyState
            text={
              query || category
                ? "没有符合条件的表情"
                : "表情库还是空的。开启群聊自动收录，或在上方上传表情。"
            }
          />
        ) : (
          <div className="meme-grid">
            {listing.data?.items.map((row) => (
              <MemeCard
                key={row.id + row.category + row.description}
                client={client}
                act={act}
                row={row}
                categories={meme.categories}
              />
            ))}
          </div>
        )}
        {listing.data && (
          <Pagination
            label="表情分页"
            page={listing.data.page}
            total={listing.data.total}
            pageSize={PAGE_SIZE}
            onChange={setPage}
            loading={listing.isFetching}
          />
        )}
      </section>
    </>
  );
}

function uniqueName(rows: MemeCategory[]): string {
  let index = rows.length + 1;
  while (rows.some((row) => row.name === `category_${index}`)) index += 1;
  return `category_${index}`;
}

function UploadForm({
  client,
  act,
  categories,
}: {
  client: ApiClient;
  act: Action;
  categories: MemeCategory[];
}) {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [reset, setReset] = useState(0);
  return (
    <form
      className="meme-upload"
      onSubmit={(event) => {
        event.preventDefault();
        if (!file) return;
        void act(async () => {
          await client.uploadMeme(file, category, description.trim());
          setFile(null);
          setDescription("");
          setReset((value) => value + 1);
        }, "表情已加入表情库");
      }}
    >
      <label className="field">
        <span className="field-name">上传表情（2 MB 以内）</span>
        <input
          key={reset}
          aria-label="表情图片"
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <label className="field">
        <span className="field-name">分类</span>
        <select
          aria-label="上传分类"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          <option value="">自动判断</option>
          {categories.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-name">描述</span>
        <input
          aria-label="上传描述"
          value={description}
          maxLength={300}
          placeholder="留空由表情转述模型描述"
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <button type="submit" className="primary" disabled={!file}>
        <Upload size={16} />
        上传
      </button>
    </form>
  );
}

function MemeImage({ client, id }: { client: ApiClient; id: string }) {
  const image = useQuery({
    queryKey: ["meme-image", id],
    queryFn: () => client.memeImage(id),
    staleTime: Infinity,
  });
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!image.data) return;
    const next = URL.createObjectURL(image.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [image.data]);
  if (image.isError) return <div className="meme-thumb muted">图片缺失</div>;
  return (
    <div className="meme-thumb">
      {url && <img src={url} alt={`表情 ${id}`} loading="lazy" />}
    </div>
  );
}

function MemeCard({
  client,
  act,
  row,
  categories,
}: {
  client: ApiClient;
  act: Action;
  row: MemeRecord;
  categories: MemeCategory[];
}) {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState(row.description);
  const [confirming, setConfirming] = useState(false);
  const known = categories.some((item) => item.name === row.category);
  return (
    <article className="meme-card">
      <MemeImage client={client} id={row.id} />
      <div className="meme-meta">
        <code>{row.id}</code>
        <span className="muted">
          {SOURCES[row.source] ?? row.source} ·{" "}
          {new Date(row.created).toLocaleDateString()}
        </span>
      </div>
      <select
        aria-label={`表情 ${row.id} 分类`}
        value={row.category}
        onChange={(event) =>
          void act(
            () => client.updateMeme(row.id, { category: event.target.value }),
            "分类已更新",
          )
        }
      >
        {!known && (
          <option value={row.category}>{row.category}（未配置）</option>
        )}
        {categories.map((item) => (
          <option key={item.name} value={item.name}>
            {item.name}
          </option>
        ))}
      </select>
      <textarea
        aria-label={`表情 ${row.id} 描述`}
        value={description}
        maxLength={300}
        rows={3}
        onChange={(event) => setDescription(event.target.value)}
      />
      <div className="actions">
        <button
          type="button"
          disabled={
            !description.trim() || description.trim() === row.description
          }
          onClick={() =>
            void act(
              () => client.updateMeme(row.id, { description }),
              "描述已保存",
            )
          }
        >
          保存描述
        </button>
        <button
          type="button"
          title="用表情转述模型重新描述"
          onClick={() =>
            void act(() => client.describeMeme(row.id), "已重新转述")
          }
        >
          <RefreshCw size={16} />
          重新转述
        </button>
        {confirming ? (
          <>
            <button
              type="button"
              className="danger"
              onClick={() =>
                void act(async () => {
                  await client.deleteMeme(row.id);
                  queryClient.removeQueries({
                    queryKey: ["meme-image", row.id],
                  });
                }, "表情已删除")
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
            aria-label={`删除表情 ${row.id}`}
            onClick={() => setConfirming(true)}
          >
            <Trash2 size={16} />
          </button>
        )}
      </div>
    </article>
  );
}
