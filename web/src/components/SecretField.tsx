import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { isSecretRef } from "../lib/utils";

export function SecretField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const saved = isSecretRef(value);
  return (
    <div className="secret-field">
      <div className="inline">
        <input
          aria-label="密钥"
          type={visible ? "text" : "password"}
          autoComplete="new-password"
          value={saved ? "" : value}
          placeholder={saved ? "已配置 · 输入新值可替换" : "直接输入密钥"}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          aria-label={visible ? "隐藏密钥" : "显示密钥"}
          onClick={() => setVisible((current) => !current)}
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
