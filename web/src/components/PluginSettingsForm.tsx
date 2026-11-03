import type { PluginConfig, PluginInfo } from "../api/types";
import { JsonSetting, SchemaFields } from "./PluginSchemaField";

export function PluginSettingsForm({
  plugin,
  metadata,
  onChange,
}: {
  plugin: PluginConfig;
  metadata?: PluginInfo;
  onChange: (settings: Record<string, unknown>) => void;
}) {
  const properties = metadata?.settings_schema?.properties ?? {};
  const record = { ...metadata?.defaults, ...plugin.settings };
  return (
    <>
      <SchemaFields
        properties={properties}
        value={record}
        onChange={onChange}
        extraKeys={Object.keys(record)}
      />
      <details>
        <summary>编辑完整配置 JSON</summary>
        <JsonSetting
          label="插件配置 JSON"
          value={record}
          onChange={(value) => onChange(value as Record<string, unknown>)}
        />
      </details>
    </>
  );
}
