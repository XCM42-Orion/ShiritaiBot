import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { Toast } from "../components/Toast";
import type { NoticeTone } from "../components/Toast";

export interface Notice {
  id: number;
  text: string;
  tone: NoticeTone;
}

const SUCCESS_TIMEOUT = 5000;

const NoticeContext = createContext<{
  succeed: (text: string) => void;
  fail: (text: string) => void;
  warn: (text: string) => void;
  dismiss: () => void;
} | null>(null);

export function NoticeProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const nextId = useRef(0);

  useEffect(() => {
    if (!notice || notice.tone !== "success") return;
    const timer = setTimeout(() => setNotice(null), SUCCESS_TIMEOUT);
    return () => clearTimeout(timer);
  }, [notice]);

  const succeed = useCallback((text: string) => {
    setNotice({ id: ++nextId.current, text, tone: "success" });
  }, []);

  const fail = useCallback((text: string) => {
    setNotice({ id: ++nextId.current, text, tone: "error" });
  }, []);

  const warn = useCallback((text: string) => {
    setNotice({ id: ++nextId.current, text, tone: "warning" });
  }, []);

  const dismiss = useCallback(() => setNotice(null), []);

  return (
    <NoticeContext.Provider value={{ succeed, fail, warn, dismiss }}>
      {children}
      {notice && (
        <Toast
          key={notice.id}
          message={notice.text}
          tone={notice.tone}
          onDismiss={dismiss}
        />
      )}
    </NoticeContext.Provider>
  );
}

export function useNotice() {
  const context = useContext(NoticeContext);
  if (!context) throw new Error("useNotice requires NoticeProvider");
  return context;
}
