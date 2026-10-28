import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { Settings } from "../api/types";

/**
 * Holds an editable copy of the server configuration. Edits stay local until
 * save() is called; the query cache keeps the last saved version.
 */
export function useConfigDraft(client: ApiClient | null) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["config"],
    queryFn: () => client!.config(),
    enabled: client !== null,
  });
  const [draft, setDraft] = useState<Settings | null>(null);
  const config = draft ?? query.data ?? null;

  const update = useCallback((next: Settings) => setDraft(next), []);
  const discard = useCallback(() => setDraft(null), []);
  const save = useCallback(async () => {
    if (!client || !config) return;
    await client.saveConfig(config);
    // Refresh the server copy before dropping the draft so the form never
    // flashes the previous values for one frame.
    await queryClient.invalidateQueries({ queryKey: ["config"] });
    setDraft(null);
  }, [client, config, queryClient]);

  return {
    config,
    loading: query.isPending,
    dirty: draft !== null,
    error: query.error instanceof Error ? query.error : null,
    update,
    discard,
    save,
  };
}
