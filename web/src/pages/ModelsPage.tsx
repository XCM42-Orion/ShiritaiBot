import { useState } from "react";
import { Check, Download, Plus, Search, Trash2 } from "lucide-react";
import type { ModelConfig, ProviderConfig } from "../api/types";
import { Field, jsonObjectError } from "../components/FieldForm";
import { ModelTestButton } from "../components/ModelTestButton";
import { EmptyState } from "../components/EmptyState";
import {
  MODEL_FIELDS,
  PROVIDER_FIELDS,
  createProvider,
} from "../config/schema";
import { uniqueId } from "../lib/utils";
import type { ConfigPageProps } from "./common";

const ROUTE_LABELS: Record<string, string> = {
  agent: "Agent 私人助手",
  chat: "Chat 群聊",
  light: "主动事件分诊",
};

export function ModelsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState<"provider" | "routes">("provider");
  const [remote, setRemote] = useState<{
    source: string;
    rows: { id: string; name: string; max_output: number }[];
  } | null>(null);
  const [fetching, setFetching] = useState(false);
  const [query, setQuery] = useState("");

  const index = Math.min(selected, Math.max(0, config.providers.length - 1));
  const provider = config.providers[index];
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
    };
    const routes = { ...config.routes };
    for (const key of ["agent", "chat", "light"]) {
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
    <div className="config-workspace">
      <section className="config-sidebar" aria-label="提供商列表">
        <div className="panel-head">
          <h2>提供商</h2>
          <button
            type="button"
            aria-label="新增提供商"
            onClick={() => {
              setConfig({
                ...config,
                providers: [
                  ...config.providers,
                  createProvider(config.providers),
                ],
              });
              setSelected(config.providers.length);
              setTab("provider");
            }}
          >
            <Plus size={16} />
          </button>
        </div>
        {config.providers.map((item, position) => (
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
        {config.providers.length === 0 && (
          <p className="muted sidebar-hint">添加提供商，连接你的第一个模型。</p>
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
            <small>主模型与故障备用顺序</small>
          </span>
        </button>
      </section>
      <section className="config-editor">
        {tab === "routes" ? (
          <>
            <h2>模型路由</h2>
            <p className="muted">
              从上到下依次尝试。添加首个模型时，会自动设为三条路由的默认模型。
            </p>
            {Object.entries(config.routes).map(([key, route]) => (
              <div className="route-block" key={key}>
                <h3>{ROUTE_LABELS[key] ?? key}</h3>
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
                            [key]: route.filter((_, item) => item !== position),
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
                    .map((model) => (
                      <option key={model.id}>{model.id}</option>
                    ))}
                </select>
              </div>
            ))}
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
              {PROVIDER_FIELDS.map((spec) => (
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
                              {added ? <Check size={16} /> : <Plus size={16} />}
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
                model.provider === provider.id ? (
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
                      {MODEL_FIELDS.map((spec) => (
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
  );
}
