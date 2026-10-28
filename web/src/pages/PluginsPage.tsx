import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import { PluginSettingsForm } from "../components/PluginSettingsForm";
import { PLUGIN_FIELDS, createPlugin, pluginTemplate } from "../config/schema";
import type { ConfigPageProps } from "./common";

export function PluginsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const [selected, setSelected] = useState(0);
  const index = Math.min(selected, Math.max(0, config.plugins.length - 1));
  const plugin = config.plugins[index];

  function update(patch: Record<string, unknown>) {
    setConfig({
      ...config,
      plugins: config.plugins.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    });
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>插件配置</h2>
        <button
          type="button"
          onClick={() => {
            setConfig({
              ...config,
              plugins: [...config.plugins, createPlugin(config.plugins)],
            });
            setSelected(config.plugins.length);
          }}
        >
          <Plus size={14} />
          添加
        </button>
      </div>
      {config.plugins.length === 0 ? (
        <EmptyState text="可以把插件放到数据目录的 plugins/ 下，再在这里配置。" />
      ) : (
        <div className="config-workspace">
          <section className="config-sidebar" aria-label="插件列表">
            {config.plugins.map((item, position) => (
              <button
                type="button"
                key={position}
                className={
                  "config-item" + (position === index ? " selected" : "")
                }
                onClick={() => setSelected(position)}
              >
                <span className={"dot " + (item.enabled ? "on" : "")} />
                <span>
                  <strong>{item.id || "未命名插件"}</strong>
                  <small>{item.enabled ? "已启用" : "已停用"}</small>
                </span>
              </button>
            ))}
          </section>
          <section className="config-editor">
            {plugin && (
              <>
                <div className="panel-head">
                  <div>
                    <h2>{plugin.id || "未命名插件"}</h2>
                    <p className="muted">
                      插件与主程序同进程运行，只安装信任的插件。
                    </p>
                  </div>
                  <button
                    type="button"
                    className="danger"
                    onClick={() =>
                      setConfig({
                        ...config,
                        plugins: config.plugins.filter(
                          (_, position) => position !== index,
                        ),
                      })
                    }
                  >
                    <Trash2 size={14} />
                    移除
                  </button>
                </div>
                <div className="field-grid">
                  {PLUGIN_FIELDS.map((spec) => (
                    <Field
                      key={spec.key}
                      spec={spec}
                      value={plugin[spec.key as "id" | "enabled"]}
                      onChange={(next) => update({ [spec.key]: next })}
                    />
                  ))}
                </div>
                <h3>插件设置</h3>
                <PluginSettingsForm
                  plugin={plugin}
                  onChange={(settings) => update({ settings })}
                />
                <div className="actions">
                  <button
                    type="button"
                    disabled={!pluginTemplate(plugin.id)}
                    onClick={() => {
                      const template = pluginTemplate(plugin.id);
                      if (template) update({ settings: template });
                    }}
                  >
                    填入配置模板
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      void act(
                        () => client.reloadPlugin(plugin.id),
                        "已重载已保存的配置",
                      )
                    }
                  >
                    重载已保存的配置
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
