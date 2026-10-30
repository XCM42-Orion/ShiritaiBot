import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { createClient } from "../api/client";

export function LoginView({
  onAuthenticated,
  onError,
}: {
  onAuthenticated: (token: string) => void;
  onError: (message: string) => void;
}) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      // Validate before persisting the token so a typo never sticks around.
      await createClient(() => token).health();
      onAuthenticated(token);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="brand-mark">
        <img src="/icon.png" alt="" />
      </div>
      <h1>ShiritaiBot</h1>
      <p className="muted">输入本机管理令牌，进入控制台。</p>
      <form onSubmit={submit}>
        <label>
          管理令牌
          <input
            autoFocus
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴本机管理令牌"
            required
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "验证中…" : "进入控制台"}
          <ArrowUpRight size={16} />
        </button>
      </form>
      <p className="muted">
        在终端运行 <code>uv run shiritaibot token</code> 获取令牌。
      </p>
    </div>
  );
}
