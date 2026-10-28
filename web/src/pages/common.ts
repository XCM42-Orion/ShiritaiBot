import type { ApiClient } from "../api/client";
import type { Settings } from "../api/types";
import type { PageId } from "../navigation";

/**
 * Wraps a mutation with busy state, error reporting and cache invalidation.
 * Pass an empty successText to skip the success toast (e.g. chat messages).
 */
export type Action = (
  work: () => Promise<unknown>,
  successText?: string,
) => Promise<void>;

export interface PageProps {
  client: ApiClient;
  act: Action;
}

export interface ConfigPageProps extends PageProps {
  config: Settings;
  setConfig: (next: Settings) => void;
}

export interface NavigationProps {
  onNavigate: (page: PageId) => void;
}
