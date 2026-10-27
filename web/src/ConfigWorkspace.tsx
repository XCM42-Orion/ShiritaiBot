import { useState } from "react";
import {
  Plus,
  Trash2,
  Search,
  Download,
  Check,
  Eye,
  EyeOff,
} from "lucide-react";

type Json = any;

export function SecretField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const saved = /^(secret:|env:)/.test(value);
  return (
    <div className="secret-field">
      <div className="inline">
        <input
          aria-label="密钥"
          type={visible ? "text" : "password"}
          autoComplete="new-password"
          value={saved ? "" : value}
          placeholder={saved ? "已配置 · 输入新值可替换" : "直接输入密钥"}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          aria-label={visible ? "隐藏密钥" : "显示密钥"}
          onClick={() => setVisible(!visible)}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
        {value && (
          <button type="button" onClick={() => onChange("")}>
            清除
          </button>
        )}
      </div>
      <small className="muted">保存后加密存储，已有密钥不会回显。</small>
    </div>
  );
}

export function uniqueId(base: string, rows: Json[]) {
  const safe = base.replace(/[^a-zA-Z0-9_.-]/g, "-").slice(0, 110) || "model";
  let id = safe,
    suffix = 2;
  while (rows.some((row) => row.id === id)) id = `${safe}-${suffix++}`;
  return id;
}

export function ProviderWorkspace({
  config,
  setConfig,
  api,
  act,
}: {
  config: Json;
  setConfig: (v: Json) => void;
  api: (path: string, method?: string, body?: Json) => Promise<Json>;
  act: (work: () => Promise<unknown>) => void;
}) {
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState("provider");
  const [remote, setRemote] = useState<{ source: string; rows: Json[] } | null>(
    null,
  );
  const [fetching, setFetching] = useState(false);
  const [query, setQuery] = useState("");
  const index = Math.min(selected, Math.max(0, config.providers.length - 1));
  const provider = config.providers[index];
  const source = provider ? JSON.stringify(provider) : "";
  const results = remote?.source === source ? remote.rows : null;
  function updateProvider(key: string, value: Json) {
    const next = { ...provider, [key]: value };
    setConfig({
      ...config,
      providers: config.providers.map((p: Json, i: number) =>
        i === index ? next : p,
      ),
      models: config.models.map((m: Json) =>
        key === "id" && m.provider === provider.id
          ? { ...m, provider: value }
          : m,
      ),
    });
  }
  function removeModels(ids: string[]) {
    return Object.fromEntries(
      Object.entries(config.routes).map(([key, route]) => [
        key,
        (route as string[]).filter((id) => !ids.includes(id)),
      ]),
    );
  }
  function addModel(row?: Json) {
    const id = uniqueId(row?.id || "model-new", config.models);
    const model = {
      id,
      provider: provider.id,
      model: row?.id || "",
      enabled: true,
      max_output: Math.min(128000, Math.max(64, row?.max_output || 4096)),
    };
    const routes = { ...config.routes };
    for (const key of ["agent", "chat", "light"])
      if (!routes[key]?.length) routes[key] = [id];
    setConfig({ ...config, models: [...config.models, model], routes });
  }
  function updateModel(index: number, key: string, value: Json) {
    const old = config.models[index];
    const routes =
      key === "id"
        ? Object.fromEntries(
            Object.entries(config.routes).map(([route, ids]) => [
              route,
              (ids as string[]).map((id) => (id === old.id ? value : id)),
            ]),
          )
        : config.routes;
    setConfig({
      ...config,
      routes,
      models: config.models.map((m: Json, i: number) =>
        i === index ? { ...m, [key]: value } : m,
      ),
    });
  }
  return (
    <div className="config-workspace">
      <section className="config-sidebar">
        <div className="panel-head">
          <h2>提供商</h2>
          <button
            aria-label="新增提供商"
            onClick={() => {
              setConfig({
                ...config,
                providers: [
                  ...config.providers,
                  {
                    id: uniqueId("provider-new", config.providers),
                    enabled: true,
                    protocol: "chat_completions",
                    base_url: "https://api.openai.com/v1",
                    api_key: "",
                    timeout: 120,
                  },
                ],
              });
              setSelected(config.providers.length);
              setTab("provider");
            }}
          >
            <Plus size={16} />
          </button>
        </div>
        {config.providers.map((p: Json, i: number) => (
          <button
            className={`config-item ${index === i && tab === "provider" ? "selected" : ""}`}
            key={i}
            onClick={() => {
              setSelected(i);
              setTab("provider");
              setQuery("");
            }}
          >
            <span className={`dot ${p.enabled ? "on" : ""}`} />
            <span>
              <strong>{p.id || "新提供商"}</strong>
              <small>{p.base_url}</small>
            </span>
          </button>
        ))}
        {!config.providers.length && (
          <p className="muted">添加提供商，连接你的第一个模型。</p>
        )}
        <button
          className={`config-item route-item ${tab === "routes" ? "selected" : ""}`}
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
                <h3>
                  {{
                    agent: "Agent 私人助手",
                    chat: "Chat 群聊",
                    light: "主动事件分诊",
                  }[key] || key}
                </h3>
                {(route as string[]).map((id, i) => (
                  <div className="inline" key={i}>
                    <span>
                      {i + 1}. {id}
                    </span>
                    <button
                      aria-label={`上移 ${id}`}
                      disabled={!i}
                      onClick={() => {
                        const next = [...(route as string[])];
                        [next[i - 1], next[i]] = [next[i], next[i - 1]];
                        setConfig({
                          ...config,
                          routes: { ...config.routes, [key]: next },
                        });
                      }}
                    >
                      上移
                    </button>
                    <button
                      aria-label={`移除路由 ${id}`}
                      onClick={() =>
                        setConfig({
                          ...config,
                          routes: {
                            ...config.routes,
                            [key]: (route as string[]).filter(
                              (_, j) => j !== i,
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
                  aria-label={`添加 ${key} 路由模型`}
                  value=""
                  onChange={(e) =>
                    e.target.value &&
                    setConfig({
                      ...config,
                      routes: {
                        ...config.routes,
                        [key]: [...(route as string[]), e.target.value],
                      },
                    })
                  }
                >
                  <option value="">＋ 添加模型</option>
                  {config.models
                    .filter((m: Json) => !(route as string[]).includes(m.id))
                    .map((m: Json) => (
                      <option key={m.id}>{m.id}</option>
                    ))}
                </select>
              </div>
            ))}
          </>
        ) : provider ? (
          <>
            <div className="panel-head">
              <div>
                <h2>{provider.id || "新提供商"}</h2>
                <p className="muted">{provider.protocol}</p>
              </div>
              <button
                className="danger"
                aria-label="移除提供商"
                onClick={() => {
                  const ids = config.models
                    .filter((m: Json) => m.provider === provider.id)
                    .map((m: Json) => m.id);
                  setConfig({
                    ...config,
                    providers: config.providers.filter(
                      (_: Json, i: number) => i !== index,
                    ),
                    models: config.models.filter(
                      (m: Json) => !ids.includes(m.id),
                    ),
                    routes: removeModels(ids),
                  });
                }}
              >
                <Trash2 size={16} /> 移除
              </button>
            </div>
            <div className="setting-row">
              <div>
                <strong>名称 / ID</strong>
                <small>字母、数字、点、下划线或短横线，最长 128 个字符。</small>
              </div>
              <input
                aria-label="提供商名称"
                value={provider.id}
                onChange={(e) => updateProvider("id", e.target.value)}
              />
            </div>
            <div className="setting-row">
              <strong>接口协议</strong>
              <select
                aria-label="接口协议"
                value={provider.protocol}
                onChange={(e) => updateProvider("protocol", e.target.value)}
              >
                {["chat_completions", "responses", "anthropic", "gemini"].map(
                  (p) => (
                    <option key={p}>{p}</option>
                  ),
                )}
              </select>
            </div>
            <div className="setting-row">
              <strong>API Key</strong>
              <SecretField
                value={provider.api_key}
                onChange={(value) => updateProvider("api_key", value)}
              />
            </div>
            <div className="setting-row">
              <div>
                <strong>API Base URL</strong>
                <small>
                  填写接口前缀：OpenAI / Anthropic 通常以 /v1 结尾，Gemini 以
                  /v1beta 结尾。
                </small>
              </div>
              <input
                aria-label="API Base URL"
                value={provider.base_url}
                onChange={(e) => updateProvider("base_url", e.target.value)}
              />
            </div>
            <div className="setting-row">
              <strong>超时（秒）</strong>
              <input
                aria-label="超时"
                type="number"
                min={5}
                max={600}
                value={provider.timeout}
                onChange={(e) =>
                  updateProvider("timeout", Number(e.target.value))
                }
              />
            </div>
            <label className="setting-row">
              <strong>启用提供商</strong>
              <input
                type="checkbox"
                checked={provider.enabled}
                onChange={(e) => updateProvider("enabled", e.target.checked)}
              />
            </label>
            <section className="model-section">
              <div className="panel-head">
                <h3>模型</h3>
                <div className="inline">
                  <button
                    disabled={fetching}
                    onClick={() => {
                      setFetching(true);
                      act(async () => {
                        try {
                          const rows = await api(
                            "/providers/discover",
                            "POST",
                            provider,
                          );
                          setRemote({ source, rows });
                          return { text: `已获取 ${rows.length} 个模型` };
                        } finally {
                          setFetching(false);
                        }
                      });
                    }}
                  >
                    <Download size={15} />
                    {fetching ? "获取中…" : "获取提供商模型"}
                  </button>
                  <button onClick={() => addModel()}>
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
                    <Search size={16} />
                    <input
                      aria-label="搜索远程模型"
                      placeholder="搜索模型名称"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                  <div className="remote-list">
                    {results
                      .filter((m) =>
                        `${m.id} ${m.name}`
                          .toLowerCase()
                          .includes(query.toLowerCase()),
                      )
                      .map((row) => {
                        const added = config.models.some(
                          (m: Json) =>
                            m.provider === provider.id && m.model === row.id,
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
                              disabled={added}
                              aria-label={`添加模型 ${row.id}`}
                              onClick={() => addModel(row)}
                            >
                              {added ? <Check size={16} /> : <Plus size={16} />}
                            </button>
                          </div>
                        );
                      })}
                    {!results.length && (
                      <p className="muted">
                        提供商没有返回可用模型，可手动添加。
                      </p>
                    )}
                  </div>
                </div>
              )}
              {config.models.map(
                (m: Json, i: number) =>
                  m.provider === provider.id && (
                    <div className="model-card" key={i}>
                      <div className="panel-head">
                        <h3>{m.id}</h3>
                        <button
                          aria-label={`删除模型 ${m.id}`}
                          onClick={() =>
                            setConfig({
                              ...config,
                              models: config.models.filter(
                                (_: Json, j: number) => i !== j,
                              ),
                              routes: removeModels([m.id]),
                            })
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                      <div className="fields">
                        <label>
                          模型标识
                          <input
                            value={m.id}
                            onChange={(e) =>
                              updateModel(i, "id", e.target.value)
                            }
                          />
                        </label>
                        <label>
                          提供商模型名称
                          <input
                            value={m.model}
                            onChange={(e) =>
                              updateModel(i, "model", e.target.value)
                            }
                          />
                        </label>
                        <label>
                          最大输出 tokens
                          <input
                            type="number"
                            min={64}
                            max={128000}
                            value={m.max_output}
                            onChange={(e) =>
                              updateModel(
                                i,
                                "max_output",
                                Number(e.target.value),
                              )
                            }
                          />
                        </label>
                        <label className="toggle">
                          启用
                          <input
                            type="checkbox"
                            checked={m.enabled}
                            onChange={(e) =>
                              updateModel(i, "enabled", e.target.checked)
                            }
                          />
                        </label>
                      </div>
                      <button
                        onClick={() =>
                          act(() =>
                            api("/models/test", "POST", { provider, model: m }),
                          )
                        }
                      >
                        测试当前配置
                      </button>
                    </div>
                  ),
              )}
            </section>
          </>
        ) : (
          <div className="empty">从左侧添加一个提供商开始配置。</div>
        )}
      </section>
    </div>
  );
}
