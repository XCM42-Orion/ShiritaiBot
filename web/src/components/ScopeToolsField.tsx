import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { TextFieldList } from "./TextFieldList";

export function ScopeToolsField({
  value,
  onChange,
}: {
  value: Record<string, string[]>;
  onChange: (value: Record<string, string[]>) => void;
}) {
  const [scope, setScope] = useState("");
  const entries = Object.entries(value);

  function add() {
    const key = scope.trim();
    if (!key || key in value) return;
    onChange({ ...value, [key]: [] });
    setScope("");
  }

  return (
    <div className="field full">
      <span className="field-name">作用域工具白名单</span>
      <p className="field-help">
        作用域格式为「会话类型:连接实例:账号或群号」，星号 * 匹配任意内容。 例如{" "}
        <code>group:qq-main:123456</code> 是某个群，
        <code>group:qq-main:*</code> 是该实例的所有群聊，
        <code>private:qq-main:123456</code> 是私聊，
        <code>web:*</code> 是所有 WebUI 会话。
        空白名单禁用全部工具；多条匹配规则取交集，不能绕过群聊权限或全局禁用规则。
      </p>
      {entries.map(([key, tools]) => (
        <div className="scope-row" key={key}>
          <code>{key}</code>
          <TextFieldList
            label={key + " 的工具"}
            value={tools}
            placeholder="每行一个工具名"
            onChange={(next) => onChange({ ...value, [key]: next })}
          />
          <button
            type="button"
            className="danger"
            onClick={() => {
              const next = { ...value };
              delete next[key];
              onChange(next);
            }}
          >
            <Trash2 size={14} />
            移除
          </button>
        </div>
      ))}
      <div className="inline">
        <input
          aria-label="作用域"
          value={scope}
          placeholder="group:qq-main:*"
          onChange={(event) => setScope(event.target.value)}
        />
        <button type="button" onClick={add}>
          <Plus size={14} />
          添加作用域
        </button>
      </div>
    </div>
  );
}
