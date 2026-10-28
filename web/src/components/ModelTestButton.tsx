import { useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import type { Action } from "../pages/common";

export function ModelTestButton({
  test,
  act,
  disabled,
}: {
  test: () => Promise<unknown>;
  act: Action;
  disabled: boolean;
}) {
  const [testing, setTesting] = useState(false);
  const running = useRef(false);
  async function run() {
    if (running.current) return;
    running.current = true;
    setTesting(true);
    try {
      await act(test, "测试请求已完成");
    } finally {
      running.current = false;
      setTesting(false);
    }
  }
  return (
    <button
      type="button"
      disabled={disabled || testing}
      aria-busy={testing}
      onClick={() => void run()}
    >
      {testing && (
        <LoaderCircle size={15} className="spinner" aria-hidden="true" />
      )}
      {testing ? "测试中…" : "测试当前配置"}
    </button>
  );
}
