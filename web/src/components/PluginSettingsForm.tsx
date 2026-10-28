import type { PluginConfig } from "../api/types";
import { PLUGIN_SETTINGS_FIELDS, pluginTemplate } from "../config/schema";
import { FieldForm } from "./FieldForm";

export function PluginSettingsForm({
  plugin,
  onChange,
}: {
  plugin: PluginConfig;
  onChange: (settings: Record<string, unknown>) => void;
}) {
  const fields = PLUGIN_SETTINGS_FIELDS[plugin.id] ?? [];
  if (!fields.length) {
    return (
      <p className="muted">
        这个插件没有内置配置表单。可以在插件目录中查看它支持的 settings。
      </p>
    );
  }
  return (
    <FieldForm
      value={{ ...pluginTemplate(plugin.id), ...plugin.settings }}
      fields={fields}
      allowExtra
      onChange={onChange}
    />
  );
}
