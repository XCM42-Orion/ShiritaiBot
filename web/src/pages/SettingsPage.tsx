import { Field } from "../components/FieldForm";
import { BASIC_SETTINGS_FIELDS, PROACTIVE_FIELDS } from "../config/schema";
import type { ConfigPageProps } from "./common";

export function SettingsPage({ config, setConfig }: ConfigPageProps) {
  return (
    <>
      <section className="panel">
        <h2>基础设置</h2>
        <div className="field-grid">
          {BASIC_SETTINGS_FIELDS.map((spec) => (
            <Field
              key={spec.key}
              spec={spec}
              value={
                config[spec.key as "persona" | "tavily_key" | "concurrency"]
              }
              onChange={(next) => setConfig({ ...config, [spec.key]: next })}
            />
          ))}
        </div>
      </section>
      <section className="panel">
        <h2>主动性与打扰策略</h2>
        <p className="muted">
          主动的助手最容易变成骚扰：免打扰时段、同来源节流和紧急度一起决定是否发出通知。
        </p>
        <div className="field-grid">
          {PROACTIVE_FIELDS.map((spec) => (
            <Field
              key={spec.key}
              spec={spec}
              value={
                config.proactive[spec.key as keyof typeof config.proactive]
              }
              onChange={(next) =>
                setConfig({
                  ...config,
                  proactive: { ...config.proactive, [spec.key]: next },
                })
              }
            />
          ))}
        </div>
      </section>
    </>
  );
}
