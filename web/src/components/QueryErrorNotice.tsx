import { useEffect } from "react";
import { useNotice } from "../hooks/useNotice";

export function QueryErrorNotice({ message }: { message: string }) {
  const { fail } = useNotice();
  useEffect(() => {
    fail(message);
  }, [message, fail]);
  return null;
}
