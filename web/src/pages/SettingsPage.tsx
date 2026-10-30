import { Field, FieldForm } from "../components/FieldForm";
import { InstanceCollection } from "../components/InstanceCollection";
import {
  BASIC_SETTINGS_FIELDS,
  PROACTIVE_FIELDS,
  IDENTITY_FIELDS,
  PERSONA_FIELDS,
  CONTEXT_FIELDS,
  GROUP_CONTEXT_FIELDS,
  MEMORY_FIELDS,
  createPersona,
} from "../config/schema";
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
        <h2>称呼与唤起</h2>
        <FieldForm
          value={config}
          fields={IDENTITY_FIELDS}
          onChange={setConfig}
        />
      </section>
      <section className="panel">
        <h2>默认人格</h2>
        <label className="field">
          <span className="field-name">默认人格</span>
          <select
            aria-label="默认人格"
            value={config.default_persona}
            onChange={(event) =>
              setConfig({ ...config, default_persona: event.target.value })
            }
          >
            <option value="">基础人设</option>
            {config.personas
              .filter((item) => item.enabled)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.id}
                </option>
              ))}
          </select>
        </label>
        <p className="muted">
          使用 /personas 查看名称和标识，/persona 标识
          为当前会话切换人格；/persona default
          恢复默认。仅主人可切换，下一轮生效。
        </p>
      </section>
      <InstanceCollection
        title="人格设定"
        items={config.personas}
        create={() => createPersona(config.personas)}
        onChange={(personas) => {
          const oldDefault = config.personas.find(
            (item) => item.id === config.default_persona,
          );
          const index = oldDefault ? config.personas.indexOf(oldDefault) : -1;
          let defaultPersona = config.default_persona;
          if (oldDefault && personas.length === config.personas.length) {
            defaultPersona = personas[index]?.enabled ? personas[index].id : "";
          } else if (
            !personas.some((item) => item.id === defaultPersona && item.enabled)
          ) {
            defaultPersona = "";
          }
          setConfig({ ...config, personas, default_persona: defaultPersona });
        }}
        fields={PERSONA_FIELDS}
        subtitle={(item) => item.name}
        emptyText="添加多个人格，通过斜杠命令切换。"
      />
      <section className="panel">
        <h2>上下文管理</h2>
        <p className="muted">
          轮数或字符数超限时，仅压缩或丢弃发给模型的旧上下文。原始消息独立保留，Agent
          可用 history_query 搜索和分页读取当前会话原文，包含 /new 之前的记录。
        </p>
        <div className="field-grid">
          <label className="field">
            <span className="field-name">上下文管理策略</span>
            <select
              aria-label="上下文管理策略"
              value={config.context.strategy}
              onChange={(event) =>
                setConfig({
                  ...config,
                  context: {
                    ...config.context,
                    strategy: event.target.value as "compress" | "discard",
                  },
                })
              }
            >
              <option value="discard">超限丢弃旧轮次</option>
              <option value="compress">压缩旧轮次为摘要</option>
            </select>
          </label>
          <label className="field">
            <span className="field-name">上下文压缩模型</span>
            <select
              aria-label="上下文压缩模型"
              value={config.context.compression_model}
              onChange={(event) =>
                setConfig({
                  ...config,
                  context: {
                    ...config.context,
                    compression_model: event.target.value,
                  },
                })
              }
            >
              <option value="">使用 light 路由</option>
              {config.models
                .filter(
                  (item) =>
                    item.enabled &&
                    config.providers.some(
                      (provider) =>
                        provider.id === item.provider && provider.enabled,
                    ),
                )
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.id}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <FieldForm
          value={config.context}
          fields={CONTEXT_FIELDS}
          onChange={(context) => setConfig({ ...config, context })}
        />
        <Field
          spec={{
            key: "max_context_chars",
            label: "上下文字符上限",
            kind: "number",
            min: 4000,
            max: 1000000,
          }}
          value={config.policy.max_context_chars}
          onChange={(value) =>
            setConfig({
              ...config,
              policy: { ...config.policy, max_context_chars: Number(value) },
            })
          }
        />
      </section>
      <section className="panel">
        <h2>长期记忆</h2>
        <p className="muted">
          记忆由 Agent
          维护：私聊和外部事件会自动整理，也可以在对话里说“记住……”“忘掉……”或直接更正；有冲突时
          Agent 会问你。群聊和 Chat 模式不会读取私人记忆。
        </p>
        <FieldForm
          value={config.memory}
          fields={MEMORY_FIELDS}
          onChange={(memory) => setConfig({ ...config, memory })}
        />
      </section>
      <section className="panel">
        <h2>群聊上下文感知</h2>
        <p className="muted">
          让 Bot
          在群里被唤起时了解之前的讨论。群成员发言作为资料提供给模型，不会被当作指令，也不写入会话历史。
        </p>
        <FieldForm
          value={config.group_context}
          fields={GROUP_CONTEXT_FIELDS}
          onChange={(group_context) => setConfig({ ...config, group_context })}
        />
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
