import { useCallback, useEffect, useState } from "react";
import { hashForPage, pageFromHash } from "../navigation";
import type { PageId } from "../navigation";

/** Hash routing keeps deep links working behind FastAPI's static file handler. */
export function useHashRoute(): [PageId, (page: PageId) => void] {
  const [page, setPage] = useState<PageId>(() => pageFromHash(location.hash));

  useEffect(() => {
    const update = () => setPage(pageFromHash(location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  // Single-page navigation must not inherit the previous page's scroll offset.
  // Guarded because jsdom (used by the unit tests) has no scrollTo implementation.
  useEffect(() => {
    if (typeof window.scrollTo === "function") {
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    }
  }, [page]);

  const navigate = useCallback((next: PageId) => {
    if (location.hash !== hashForPage(next)) location.hash = hashForPage(next);
    setPage(next);
  }, []);

  return [page, navigate];
}
