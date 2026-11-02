import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import type { SkillInfo } from "../api/types";
import type { ConfigPageProps } from "./common";

export function SkillsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const queryClient = useQueryClient();
  const listing = useQuery({
    queryKey: ["skills"],
    queryFn: () => client.skills(),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [working, setWorking] = useState(false);
  const rows = listing.data ?? [];
  const current = rows.find((row) => row.name === selected) ?? rows[0];
  const detail = useQuery({
    queryKey: ["skills", current?.name, current?.source],
    queryFn: () => client.skill(current!.name),
    enabled: Boolean(current),
  });
  const skills = config.skills;

  function setSkills(patch: Partial<typeof skills>) {
    setConfig({ ...config, skills: { ...skills, ...patch } });
  }

  function toggle(name: string, enabled: boolean) {
    const rest = skills.disabled.filter((item) => item !== name);
    setSkills({ disabled: enabled ? rest : [...rest, name] });
  }

  async function mutate(work: () => Promise<SkillInfo[]>, message: string) {
    setWorking(true);
    try {
      await act(async () => {
        queryClient.setQueryData(["skills"], await work());
        setInstalling(false);
        setRemoving(null);
        setFile(null);
      }, message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>技能设置</h2>
        <Field
          spec={{
            key: "enabled",
            label: "启用技能系统",
            kind: "boolean",
            help: "开启后 Agent 模式提供 skill_list / skill_read / skill_write 等工具。",
          }}
          value={skills.enabled}
          onChange={(enabled) => setSkills({ enabled: Boolean(enabled) })}
        />
        <Field
          spec={{
            key: "inject",
            label: "在系统提示中列出技能",
            kind: "boolean",
            help: "列出已启用技能的名称和描述，让助手在合适时自动读取对应技能。",
          }}
          value={skills.inject}
          onChange={(inject) => setSkills({ inject: Boolean(inject) })}
        />
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>技能列表</h2>
          <button
            type="button"
            disabled={working}
            onClick={() => setInstalling(!installing)}
          >
            <Plus size={14} />
            安装技能
          </button>
        </div>
        {installing && (
          <form
            className="config-editor"
            onSubmit={(event) => {
              event.preventDefault();
              if (file)
                void mutate(
                  () => client.installSkill(file, replace),
                  "技能已安装",
                );
            }}
          >
            <p className="muted">
              支持 .skill / .zip 技能包或单个 SKILL.md（最大 10
              MiB）。也可以直接在 QQ
              里把技能文件发给助手，让它安装。技能附带的脚本属于外部代码，只安装信任的技能。
            </p>
            <label className="field">
              技能文件
              <input
                aria-label="技能文件"
                type="file"
                accept=".skill,.zip,.md"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <label className="field">
              <span>
                <input
                  type="checkbox"
                  checked={replace}
                  onChange={(event) => setReplace(event.target.checked)}
                />{" "}
                覆盖同名技能
              </span>
            </label>
            <div className="actions">
              <button type="submit" disabled={working || !file}>
                上传并安装
              </button>
              <button
                type="button"
                disabled={working}
                onClick={() => setInstalling(false)}
              >
                取消
              </button>
            </div>
          </form>
        )}
        {removing && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="确认删除技能"
            className="config-editor"
          >
            <h3>确认删除 {removing}？</h3>
            <p>技能目录会被删除，无法恢复。</p>
            <div className="actions">
              <button
                type="button"
                className="danger"
                disabled={working}
                onClick={() =>
                  void mutate(() => client.removeSkill(removing), "技能已删除")
                }
              >
                确认删除
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
        {listing.isPending ? (
          <p className="muted">正在读取技能…</p>
        ) : listing.isError ? (
          <p role="alert">{listing.error.message}</p>
        ) : !rows.length ? (
          <EmptyState text="尚未安装技能。" />
        ) : (
          <div className="config-workspace">
            <section className="config-sidebar" aria-label="技能列表">
              {rows.map((row) => {
                const enabled = !skills.disabled.includes(row.name);
                return (
                  <button
                    type="button"
                    key={row.name}
                    className={
                      "config-item" +
                      (row.name === current?.name ? " selected" : "")
                    }
                    onClick={() => setSelected(row.name)}
                  >
                    <span
                      className={"dot " + (enabled && !row.error ? "on" : "")}
                    />
                    <span>
                      <strong>{row.name}</strong>
                      <small>
                        {row.source === "bundled" ? "内置" : "已安装"}
                        {row.overrides ? " · 覆盖内置" : ""}
                        {row.error ? " · 无效" : enabled ? "" : " · 已停用"}
                      </small>
                    </span>
                  </button>
                );
              })}
            </section>
            <section className="config-editor">
              {current && (
                <>
                  <div className="panel-head">
                    <div>
                      <h2>{current.name}</h2>
                      <p className="muted">{current.description}</p>
                    </div>
                    {current.source === "installed" && (
                      <button
                        type="button"
                        className="danger"
                        disabled={working}
                        onClick={() => setRemoving(current.name)}
                      >
                        <Trash2 size={14} />
                        删除
                      </button>
                    )}
                  </div>
                  {current.error && <p role="alert">{current.error}</p>}
                  <Field
                    spec={{
                      key: "skill-enabled",
                      label: "启用此技能",
                      kind: "boolean",
                      help: "停用后不会出现在系统提示中，也不能被读取。修改后请保存。",
                    }}
                    value={!skills.disabled.includes(current.name)}
                    onChange={(value) => toggle(current.name, Boolean(value))}
                  />
                  <p className="muted">位置：{current.path}</p>
                  {detail.data && (
                    <>
                      <h3>文件</h3>
                      <ul className="plain-list">
                        {detail.data.files.map((item) => (
                          <li key={item.path}>
                            <code>{item.path}</code>{" "}
                            <small className="muted">{item.bytes} 字节</small>
                          </li>
                        ))}
                      </ul>
                      <h3>SKILL.md</h3>
                      <pre>{detail.data.content}</pre>
                    </>
                  )}
                  {detail.isError && <p role="alert">{detail.error.message}</p>}
                </>
              )}
            </section>
          </div>
        )}
      </section>
    </>
  );
}
