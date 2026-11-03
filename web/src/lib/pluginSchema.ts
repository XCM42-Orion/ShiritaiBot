import type { PluginSettingSchema, PluginTemplate } from "../api/types";

export function schemaLabel(name: string, schema: PluginSettingSchema) {
  return schema.title ?? schema["x-name"] ?? name;
}

/** Value used for a missing setting: the schema default, else an empty value of its type. */
export function schemaDefault(schema: PluginSettingSchema): unknown {
  if (schema.default !== undefined) return structuredClone(schema.default);
  if (schema.type === "object") {
    return Object.fromEntries(
      Object.entries(schema.properties ?? {}).map(([key, child]) => [
        key,
        schemaDefault(child),
      ]),
    );
  }
  return {
    string: "",
    integer: 0,
    number: 0,
    boolean: false,
    array: [],
  }[schema.type ?? ""];
}

export function templateEntry(
  key: string,
  template: PluginTemplate,
): Record<string, unknown> {
  return {
    __template_key: key,
    ...Object.fromEntries(
      Object.entries(template.properties).map(([name, schema]) => [
        name,
        schemaDefault(schema),
      ]),
    ),
  };
}

/** Reads a dot-notation path such as `provider.model` for template list summaries. */
export function pickPath(value: unknown, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (current, part) =>
        current && typeof current === "object"
          ? (current as Record<string, unknown>)[part]
          : undefined,
      value,
    );
}
