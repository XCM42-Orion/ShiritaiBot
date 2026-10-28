import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import { PluginSettingsForm } from "../components/PluginSettingsForm";
import type { PluginInfo } from "../api/types";
import type { ConfigPageProps } from "./common";

const statuses = {
  loaded: "运行中",
  disabled: "未启用",
  missing: "未安装",
  error: "加载失败",
};

export function PluginsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const queryClient = useQueryClient();
  const discovery = useQuery({
    queryKey: ["plugins"],
    queryFn: () => client.plugins(),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [id, setId] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState("zip");
  const latest = useRef(config);
  latest.current = config;
  const rows = discovery.data ?? [];
  const metadata = rows.find((item) => item.id === selected) ?? rows[0];
  const plugin =
    metadata &&
    (config.plugins.find((item) => item.id === metadata.id) ?? {
      id: metadata.id,
      enabled: metadata.enabled,
      settings: metadata.defaults ?? {},
    });

  function update(patch: Record<string, unknown>) {
    if (!plugin) return;
    const next = { ...plugin, ...patch };
    setConfig({
      ...config,
      plugins: config.plugins.some((item) => item.id === plugin.id)
        ? config.plugins.map((item) => (item.id === plugin.id ? next : item))
        : [...config.plugins, next],
    });
  }

  async function mutate(work: () => Promise<PluginInfo[]>, message: string) {
    setWorking(true);
    try {
      await act(async () => {
        const result = await work();
        queryClient.setQueryData(["plugins"], result);
        const saved = await client.config();
        queryClient.setQueryData(["config"], saved);
        const draft = latest.current;
        setConfig({
          ...draft,
          plugins: saved.plugins.map(
            (item) =>
              draft.plugins.find((current) => current.id === item.id) ?? item,
          ),
        });
        setInstalling(false);
        setRemoving(null);
      }, message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>插件管理</h2>
        <div className="actions">
          <button
            type="button"
            disabled={working}
            onClick={() =>
              void mutate(
                () => client.reloadPlugins(),
                "已重新扫描并重载插件，请查看各插件状态",
              )
            }
          >
            <RefreshCw size={14} />
            重载插件
          </button>
          <button
            type="button"
            disabled={working}
            onClick={() => setInstalling(!installing)}
          >
            <Plus size={14} />
            安装插件
          </button>
        </div>
      </div>
      {installing && (
        <form
          className="config-editor"
          onSubmit={(event) => {
            event.preventDefault();
            void mutate(
              () =>
                source === "github"
                  ? client.installPluginGithub(id, url)
                  : client.installPluginZip(id, file!),
              "插件已安装，请配置并启用",
            );
          }}
        >
          <p className="muted">
            插件与主程序同进程运行，只安装信任的插件。新插件默认停用，配置后启用并保存。
          </p>
          <label className="field">
            插件标识
            <input
              aria-label="安装插件标识"
              value={id}
              required
              pattern="[a-z0-9_]{1,64}"
              placeholder="例如 hello_world"
              onChange={(event) => setId(event.target.value)}
            />
          </label>
          <label className="field">
            安装来源
            <select
              aria-label="安装来源"
              value={source}
              onChange={(event) => setSource(event.target.value)}
            >
              <option value="zip">上传 ZIP 插件包</option>
              <option value="github">公开 GitHub 仓库</option>
            </select>
          </label>
          {source === "github" ? (
            <label className="field">
              GitHub 仓库地址
              <input
                key="github"
                aria-label="GitHub 仓库地址"
                type="url"
                required
                value={url}
                placeholder="https://github.com/owner/repo"
                onChange={(event) => setUrl(event.target.value)}
              />
            </label>
          ) : (
            <label className="field">
              ZIP 插件包（最大 20 MiB）
              <input
                key="zip"
                aria-label="ZIP 插件包"
                type="file"
                accept=".zip"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
          )}
          <div className="actions">
            <button
              type="submit"
              disabled={working || !id || (source === "zip" ? !file : !url)}
            >
              安装并扫描
            </button>
            <button
              type="button"
              disabled={working}
              onClick={() => setInstalling(false)}
            >
              取消安装
            </button>
          </div>
        </form>
      )}
      {removing && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="确认移除插件"
          className="config-editor"
        >
          <h3>确认移除 {removing}？</h3>
          <p>
            将停止插件、撤销工具和通知通道，删除插件代码目录及配置。插件数据和历史记录会保留。
          </p>
          <div className="actions">
            <button
              type="button"
              className="danger"
              disabled={working}
              onClick={() =>
                void mutate(
                  () => client.removePlugin(removing),
                  "插件已移除，插件列表已更新",
                )
              }
            >
              确认移除
            </button>
            <button
              type="button"
              disabled={working}
              onClick={() => setRemoving(null)}
            >
              取消
            </button>
          </div>
        </div>
      )}
      {discovery.isPending ? (
        <p className="muted">正在扫描插件目录…</p>
      ) : discovery.isError ? (
        <p role="alert">{discovery.error.message}</p>
      ) : !rows.length ? (
        <EmptyState text="尚未发现插件。可以上传 ZIP、从 GitHub 安装，或放入插件目录后点击重载插件。" />
      ) : (
        <div className="config-workspace">
          <section className="config-sidebar" aria-label="插件列表">
            {rows.map((item) => (
              <button
                type="button"
                key={item.id}
                className={
                  "config-item" + (item.id === metadata?.id ? " selected" : "")
                }
                onClick={() => setSelected(item.id)}
              >
                <span
                  className={"dot " + (item.status === "loaded" ? "on" : "")}
                />
                <span>
                  <strong>{item.name || item.id}</strong>
                  <small>
                    {item.id} · {statuses[item.status]}
                  </small>
                </span>
              </button>
            ))}
          </section>
          <section className="config-editor">
            {plugin && metadata && (
              <>
                <div className="panel-head">
                  <div>
                    <h2>{metadata.name || plugin.id}</h2>
                    <p className="muted">{metadata.description}</p>
                  </div>
                  <button
                    type="button"
                    className="danger"
                    disabled={working}
                    onClick={() => setRemoving(plugin.id)}
                  >
                    <Trash2 size={14} />
                    移除
                  </button>
                </div>
                <p className="muted">
                  插件标识：{plugin.id} · {statuses[metadata.status]}
                </p>
                {metadata.error && (
                  <p role="alert">
                    {metadata.error}
                    {metadata.status === "error" && metadata.enabled
                      ? "；若此前已运行，旧实例会继续保留。"
                      : ""}
                  </p>
                )}
                <Field
                  spec={{ key: "enabled", label: "启用插件", kind: "boolean" }}
                  value={plugin.enabled}
                  onChange={(enabled) => update({ enabled })}
                />
                <h3>插件设置</h3>
                <PluginSettingsForm
                  key={plugin.id}
                  plugin={plugin}
                  metadata={metadata}
                  onChange={(settings) => update({ settings })}
                />
                <div className="actions">
                  <button
                    type="button"
                    disabled={!Object.keys(metadata.defaults ?? {}).length}
                    onClick={() =>
                      update({ settings: { ...metadata.defaults } })
                    }
                  >
                    填入配置模板
                  </button>
                  <button
                    type="button"
                    disabled={working || metadata.status === "missing"}
                    onClick={() =>
                      void mutate(
                        () => client.reloadPlugin(plugin.id),
                        "插件已重载",
                      )
                    }
                  >
                    <RefreshCw size={14} />
                    重载当前插件
                  </button>
                </div>
                <p className="muted">
                  配置修改后请保存；手动重载使用已保存的配置，并重新读取插件代码和配置项定义。
                </p>
              </>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
