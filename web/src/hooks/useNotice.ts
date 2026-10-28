import { useCallback, useEffect, useState } from "react";

export interface Notice {
  text: string;
  error: boolean;
}

const SUCCESS_TIMEOUT = 5000;

export function useNotice() {
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    if (!notice || notice.error) return;
    const timer = setTimeout(() => setNotice(null), SUCCESS_TIMEOUT);
    return () => clearTimeout(timer);
  }, [notice]);

  const succeed = useCallback((text: string) => {
    setNotice({ text, error: false });
  }, []);

  const fail = useCallback((text: string) => {
    setNotice({ text, error: true });
  }, []);

  const dismiss = useCallback(() => setNotice(null), []);

  return { notice, succeed, fail, dismiss };
}
