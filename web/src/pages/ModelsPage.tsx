import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Download, Plus, Search, Trash2 } from "lucide-react";
import type { ModelConfig, Protocol, ProviderConfig } from "../api/types";
import { Field, FieldForm, jsonObjectError } from "../components/FieldForm";
import { ModelTestButton } from "../components/ModelTestButton";
import { EmptyState } from "../components/EmptyState";
import {
  EMBEDDING_MODEL_FIELDS,
  MODEL_FIELDS,
  PROVIDER_FIELDS,
  VISION_FIELDS,
  createProvider,
} from "../config/schema";
import { uniqueId } from "../lib/utils";
import type { ConfigPageProps } from "./common";

type Category = "text" | "decision" | "embedding";

const ROUTES: { key: string; label: string; help: string }[] = [
  {
    key: "chat",
    label: "Chat 默认模型",
    help: "做什么：Chat 模式会话（如群聊）的回复。适合：响应快、价格低、中文对话自然的模型。",
  },
  {
    key: "agent",
    label: "Agent Work 模型",
    help: "做什么：执行 Main Loop 与 Proactive Loop 的长程工作任务（多步工具调用）。适合：工具调用稳定、长程任务能力强的模型。未启用分流时，Agent 模式全部使用此模型。",
  },
  {
    key: "agent_chat",
    label: "Agent Chat 模型",
    help: "做什么：Agent 模式下日常的询问与对话。适合：世界知识丰富、适合情感陪伴的模型（例如 Gemini、Grok）。",
  },
  {
    key: "router",
    label: "工作/对话分流模型",
    help: "做什么：Agent 模式下新消息进入时判断属于 Work 还是 Chat。适合：Jev 等判断模型，或 RPS 高的小模型。",
  },
  {
    key: "light",
    label: "主动事件分诊与摘要模型",
    help: "做什么：为外部事件评估重要性；每轮短期记忆摘要和上下文压缩也使用此路由，自动审批路由留空时同样使用。适合：便宜快速的模型；可把 Jev 放在最前面做分诊，但摘要等需要生成文本，请再添加一个文本模型。",
  },
  {
    key: "memory",
    label: "记忆模型",
    help: "做什么：后台从私聊和外部事件中抽取人物、事实、事件，判断证据是否支持、是否同一人、新旧事实关系。不直接回复主人，不阻塞对话。适合：中文理解好、能稳定输出 JSON、价格适中的文本模型（抽错会长期留在记忆里，准确比快重要）；Jev 等判断模型可放在文本模型之前，失败时自动改用文本模型。留空时使用分诊与摘要模型。",
  },
  {
    key: "approval",
    label: "工具自动审批模型",
    help: "做什么：权限为“模型自动审批”的工具调用前，独立审查工具、完整参数与用户请求，决定批准或拒绝。不调用工具。适合：判断严谨、能稳定输出 JSON 的文本模型；Jev 等判断模型可放在文本模型之前，只有把握足够时直接采用，否则改用后面的文本模型。全部失败、超时或返回无效时拒绝执行。留空时使用分诊与摘要模型。",
  },
  {
    key: "vision",
    label: "图像转述模型",
    help: "做什么：主模型不支持图像输入时，把上下文中的图片描述成文字，结果按图片缓存。适合：便宜、看图准确、能转录图中文字的多模态模型。只能选择模态包含图像的模型。",
  },
  {
    key: "embedding",
    label: "Embedding 模型",
    help: "做什么：为记忆条目和查询计算向量，用于语义检索。适合：支持中文的 Embedding 模型。只使用第一个可用模型；更换后后台全量重算，期间只用关键词检索。留空时记忆检索只用名称链接和关键词。",
  },
];

// Decision models (systemone) classify only, so they fit just these routes.
const DECISION_ROUTES = ["router", "light", "memory", "approval"];
const EMBEDDING_PROTOCOLS: Protocol[] = [
  "chat_completions",
  "responses",
  "gemini",
];
const TEXT_PROTOCOLS: Protocol[] = [
  "chat_completions",
  "responses",
  "anthropic",
  "gemini",
];
const CATEGORIES: {
  key: Category;
  label: string;
  routes: string[];
  protocols: Protocol[];
}[] = [
  {
    key: "text",
    label: "语言模型",
    routes: [
      "chat",
      "agent",
      "agent_chat",
      "router",
      "light",
      "memory",
      "approval",
      "vision",
    ],
    protocols: TEXT_PROTOCOLS,
  },
  {
    key: "decision",
    label: "判断模型（Jev）",
    routes: DECISION_ROUTES,
    protocols: ["systemone"],
  },
  {
    key: "embedding",
    label: "Embedding",
    routes: ["embedding"],
    protocols: EMBEDDING_PROTOCOLS,
  },
];
const INTRO: Record<Category, string> = {
  text: "生成文本、调用工具的模型。",
  decision:
    "Jev 等判断模型（systemone 协议）只做分类和打分，不生成文本，只能用于分流、分诊、记忆和自动审批路由；判断失败时改用同一路由里的文本模型。",
  embedding:
    "为长期记忆计算向量的模型，支持 OpenAI 兼容 /embeddings 与 Gemini 接口；可以复用语言模型的提供商。",
};

function modelFits(model: ModelConfig, category: Category) {
  return (model.kind === "embedding") === (category === "embedding");
}

export function ModelsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const [category, setCategory] = useState<Category>("text");
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState<"provider" | "routes">("provider");
  const [remote, setRemote] = useState<{
    source: string;
    rows: { id: string; name: string; max_output: number }[];
  } | null>(null);
  const [fetching, setFetching] = useState(false);
  const [query, setQuery] = useState("");

  const current = CATEGORIES.find((item) => item.key === category)!;
  const visible = config.providers
    .map((item, position) => ({ item, position }))
    .filter(({ item }) => current.protocols.includes(item.protocol));
  const index =
    visible.find(({ position }) => position === selected)?.position ??
    visible[0]?.position ??
    -1;
  const provider = index >= 0 ? config.providers[index] : undefined;
  const status = useQuery({
    queryKey: ["memory-status"],
    queryFn: () => client.memoryStatus(),
    enabled: category === "embedding",
    refetchInterval: 10000,
  });

  function routeAccepts(key: string, model: ModelConfig) {
    if (model.kind === "embedding") return key === "embedding";
    if (key === "embedding") return false;
    if (key === "vision" && !model.modalities.includes("image")) return false;
    const protocol = config.providers.find(
      (item) => item.id === model.provider,
    )?.protocol;
    return protocol !== "systemone" || DECISION_ROUTES.includes(key);
  }
  const source = provider ? JSON.stringify(provider) : "";
  const results = remote?.source === source ? remote.rows : null;

  function updateProvider(patch: Partial<ProviderConfig>) {
    if (!provider) return;
    const next = { ...provider, ...patch };
    setConfig({
      ...config,
      providers: config.providers.map((item, position) =>
        position === index ? next : item,
      ),
      models: config.models.map((model) =>
        patch.id !== undefined && model.provider === provider.id
          ? { ...model, provider: patch.id }
          : model,
      ),
    });
  }

  function routesWithout(ids: string[]) {
    return Object.fromEntries(
      Object.entries(config.routes).map(([route, models]) => [
        route,
        models.filter((id) => !ids.includes(id)),
      ]),
    );
  }

  function addModel(row?: { id: string; max_output: number }) {
    if (!provider) return;
    const id = uniqueId(row?.id || "model-new", config.models);
    const model: ModelConfig = {
      id,
      provider: provider.id,
      model: row?.id || "",
      enabled: true,
      max_output: Math.min(128000, Math.max(64, row?.max_output || 4096)),
      max_context: 0,
      modalities: ["text"],
      extra_body: {},
      kind: category === "embedding" ? "embedding" : "text",
      dims: 0,
    };
    const routes = { ...config.routes };
    const defaults = {
      text: ["agent", "chat", "light"],
      decision: ["light"],
      embedding: ["embedding"],
    }[category];
    for (const key of defaults) {
      if (!routes[key]?.length) routes[key] = [id];
    }
    setConfig({ ...config, models: [...config.models, model], routes });
  }

  function updateModel(position: number, patch: Record<string, unknown>) {
    const old = config.models[position];
    const routes =
      patch.id !== undefined
        ? Object.fromEntries(
            Object.entries(config.routes).map(([route, ids]) => [
              route,
              ids.map((id) => (id === old.id ? String(patch.id) : id)),
            ]),
          )
        : config.routes;
    setConfig({
      ...config,
      routes,
      models: config.models.map((model, itemIndex) =>
        itemIndex === position ? { ...model, ...patch } : model,
      ),
    });
  }

  return (
    <>
      <div className="range-tabs model-tabs" role="group" aria-label="模型类型">
        {CATEGORIES.map(({ key, label }) => (
          <button
            type="button"
            key={key}
            className={category === key ? "selected" : ""}
            aria-pressed={category === key}
            onClick={() => {
              setCategory(key);
              setRemote(null);
              setQuery("");
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="muted model-intro">{INTRO[category]}</p>
      <div className="config-workspace">
        <section className="config-sidebar" aria-label="提供商列表">
          <div className="panel-head">
            <h2>提供商</h2>
            <button
              type="button"
              aria-label="新增提供商"
              onClick={() => {
                const created = createProvider(config.providers);
                setConfig({
                  ...config,
                  providers: [
                    ...config.providers,
                    category === "decision"
                      ? {
                          ...created,
                          protocol: "systemone",
                          base_url: "https://api.typesafe.ai/v1",
                        }
                      : created,
                  ],
                });
                setSelected(config.providers.length);
                setTab("provider");
              }}
            >
              <Plus size={16} />
            </button>
          </div>
          {visible.map(({ item, position }) => (
            <button
              type="button"
              key={position}
              className={
                "config-item" +
                (index === position && tab === "provider" ? " selected" : "")
              }
              onClick={() => {
                setSelected(position);
                setTab("provider");
                setQuery("");
              }}
            >
              <span className={"dot " + (item.enabled ? "on" : "")} />
              <span>
                <strong>{item.id || "未命名"}</strong>
                <small>{item.base_url}</small>
              </span>
            </button>
          ))}
          {visible.length === 0 && (
            <p className="muted sidebar-hint">
              {category === "decision"
                ? "添加 systemone 协议的提供商（如 TypeSafe）。"
                : "添加提供商，连接你的第一个模型。"}
            </p>
          )}
          <button
            type="button"
            className={
              "config-item route-item" + (tab === "routes" ? " selected" : "")
            }
            onClick={() => setTab("routes")}
          >
            <span>
              <strong>模型路由</strong>
              <small>用途说明与备用顺序</small>
            </span>
          </button>
        </section>
        <section className="config-editor">
          {tab === "routes" ? (
            <>
              <h2>模型路由</h2>
              <p className="muted">
                每条路由从上到下依次尝试；同一条路由在各标签页中是同一份配置。
                {category === "text" &&
                  "添加首个模型时，会自动设为 Chat、Agent Work 和分诊路由的默认模型。"}
              </p>
              {category === "embedding" && (
                <p className="muted" aria-label="记忆向量状态">
                  {status.data
                    ? status.data.model
                      ? `当前向量模型 ${status.data.model}：已索引 ${status.data.indexed} 条，待计算 ${status.data.pending} 条`
                      : `未配置 Embedding：${status.data.pending} 条记忆只用名称链接和关键词检索`
                    : "正在读取记忆向量状态…"}
                </p>
              )}
              {category === "text" && (
                <div className="field field-toggle route-split">
                  <div>
                    <span className="field-name">
                      启用 Agent Work/Chat 分流
                    </span>
                    <small className="field-help">
                      开启后由分流模型判断每条新消息属于 Work 还是 Chat；关闭时
                      Agent 模式统一使用 Agent Work 模型。
                    </small>
                  </div>
                  <input
                    aria-label="启用 Agent Work/Chat 分流"
                    type="checkbox"
                    checked={Boolean(config.agent_split)}
                    onChange={(event) =>
                      setConfig({
                        ...config,
                        agent_split: event.target.checked,
                      })
                    }
                  />
                </div>
              )}
              {ROUTES.filter(({ key }) => current.routes.includes(key)).map(
                ({ key, label, help }) => {
                  const route = config.routes[key] ?? [];
                  const inactive =
                    !config.agent_split &&
                    (key === "agent_chat" || key === "router");
                  return (
                    <div
                      className={"route-block" + (inactive ? " inactive" : "")}
                      key={key}
                    >
                      <h3>{label}</h3>
                      {help && <p className="muted route-help">{help}</p>}
                      {route.map((id, position) => (
                        <div className="inline" key={id + position}>
                          <span>
                            {position + 1}. {id}
                          </span>
                          <button
                            type="button"
                            aria-label={"上移 " + id}
                            disabled={position === 0}
                            onClick={() => {
                              const next = [...route];
                              [next[position - 1], next[position]] = [
                                next[position],
                                next[position - 1],
                              ];
                              setConfig({
                                ...config,
                                routes: { ...config.routes, [key]: next },
                              });
                            }}
                          >
                            上移
                          </button>
                          <button
                            type="button"
                            aria-label={"移除路由 " + id}
                            onClick={() =>
                              setConfig({
                                ...config,
                                routes: {
                                  ...config.routes,
                                  [key]: route.filter(
                                    (_, item) => item !== position,
                                  ),
                                },
                              })
                            }
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ))}
                      <select
                        aria-label={"添加 " + key + " 路由模型"}
                        value=""
                        onChange={(event) => {
                          if (!event.target.value) return;
                          setConfig({
                            ...config,
                            routes: {
                              ...config.routes,
                              [key]: [...route, event.target.value],
                            },
                          });
                        }}
                      >
                        <option value="">＋ 添加模型</option>
                        {config.models
                          .filter((model) => !route.includes(model.id))
                          .filter((model) => routeAccepts(key, model))
                          .map((model) => (
                            <option key={model.id}>{model.id}</option>
                          ))}
                      </select>
                      {key === "vision" && (
                        <FieldForm
                          value={config.vision}
                          fields={VISION_FIELDS}
                          onChange={(vision) =>
                            setConfig({ ...config, vision })
                          }
                        />
                      )}
                    </div>
                  );
                },
              )}
            </>
          ) : provider ? (
            <>
              <div className="panel-head">
                <div>
                  <h2>{provider.id || "未命名提供商"}</h2>
                  <p className="muted">{provider.protocol}</p>
                </div>
                <button
                  type="button"
                  className="danger"
                  aria-label="移除提供商"
                  onClick={() => {
                    const ids = config.models
                      .filter((model) => model.provider === provider.id)
                      .map((model) => model.id);
                    setConfig({
                      ...config,
                      providers: config.providers.filter(
                        (_, position) => position !== index,
                      ),
                      models: config.models.filter(
                        (model) => !ids.includes(model.id),
                      ),
                      routes: routesWithout(ids),
                    });
                  }}
                >
                  <Trash2 size={16} />
                  移除
                </button>
              </div>
              <div className="field-grid">
                {PROVIDER_FIELDS.map((spec) =>
                  spec.key === "protocol"
                    ? { ...spec, options: current.protocols }
                    : spec,
                ).map((spec) => (
                  <Field
                    key={spec.key}
                    spec={spec}
                    value={provider[spec.key as keyof ProviderConfig]}
                    onChange={(next) =>
                      updateProvider({
                        [spec.key]: next,
                      } as Partial<ProviderConfig>)
                    }
                  />
                ))}
              </div>
              <section className="model-section">
                <div className="panel-head">
                  <h3>模型</h3>
                  <div className="inline">
                    <button
                      type="button"
                      disabled={fetching}
                      onClick={() => {
                        setFetching(true);
                        void act(async () => {
                          try {
                            const rows = await client.discoverModels(provider);
                            setRemote({ source, rows });
                            return rows.length + " 个模型";
                          } finally {
                            setFetching(false);
                          }
                        }, "已获取模型列表");
                      }}
                    >
                      <Download size={15} />
                      {fetching ? "获取中…" : "获取提供商模型"}
                    </button>
                    <button type="button" onClick={() => addModel()}>
                      <Plus size={15} />
                      手动添加
                    </button>
                  </div>
                </div>
                <p className="muted">
                  使用当前填写的地址和密钥获取，无需先保存。点击右侧 ＋
                  添加，再保存配置。
                </p>
                {results && (
                  <div className="remote-models">
                    <label className="inline">
                      <Search size={16} aria-hidden="true" />
                      <input
                        aria-label="搜索远程模型"
                        placeholder="搜索模型名称"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                    </label>
                    <div className="remote-list">
                      {results
                        .filter((row) =>
                          (row.id + " " + row.name)
                            .toLowerCase()
                            .includes(query.toLowerCase()),
                        )
                        .map((row) => {
                          const added = config.models.some(
                            (model) =>
                              model.provider === provider.id &&
                              model.model === row.id,
                          );
                          return (
                            <div className="list-row" key={row.id}>
                              <div>
                                <strong>{row.id}</strong>
                                <small>
                                  {row.name !== row.id ? row.name : ""}
                                </small>
                              </div>
                              <button
                                type="button"
                                disabled={added}
                                aria-label={"添加模型 " + row.id}
                                onClick={() => addModel(row)}
                              >
                                {added ? (
                                  <Check size={16} />
                                ) : (
                                  <Plus size={16} />
                                )}
                              </button>
                            </div>
                          );
                        })}
                      {results.length === 0 && (
                        <p className="muted">
                          提供商没有返回可用模型，可手动添加。
                        </p>
                      )}
                    </div>
                  </div>
                )}
                {config.models.map((model, position) =>
                  model.provider === provider.id &&
                  modelFits(model, category) ? (
                    <div className="model-card" key={position}>
                      <div className="panel-head">
                        <h3>{model.id}</h3>
                        <button
                          type="button"
                          aria-label={"删除模型 " + model.id}
                          onClick={() =>
                            setConfig({
                              ...config,
                              models: config.models.filter(
                                (_, itemIndex) => itemIndex !== position,
                              ),
                              routes: routesWithout([model.id]),
                            })
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                      <div className="field-grid">
                        {(category === "embedding"
                          ? EMBEDDING_MODEL_FIELDS
                          : MODEL_FIELDS
                        ).map((spec) => (
                          <Field
                            key={spec.key}
                            spec={spec}
                            value={model[spec.key as keyof typeof model]}
                            onChange={(next) =>
                              updateModel(position, { [spec.key]: next })
                            }
                          />
                        ))}
                      </div>
                      <ModelTestButton
                        test={() => client.testModel(provider, model)}
                        act={act}
                        disabled={Boolean(jsonObjectError(model.extra_body))}
                      />
                    </div>
                  ) : null,
                )}
              </section>
            </>
          ) : (
            <EmptyState text="从左侧添加一个提供商开始配置。" />
          )}
        </section>
      </div>
    </>
  );
}
