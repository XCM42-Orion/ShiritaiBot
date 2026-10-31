import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createClient } from "./api/client";
import type { ApiClient } from "./api/client";
import { AppShell } from "./components/AppShell";
import { LoginView } from "./components/LoginView";
import { SaveBar } from "./components/SaveBar";
import { useConfigDraft } from "./hooks/useConfigDraft";
import { useHashRoute } from "./hooks/useHashRoute";
import { NoticeProvider, useNotice } from "./hooks/useNotice";
import { useRealtime } from "./hooks/useRealtime";
import type { RealtimeEvent } from "./hooks/useRealtime";
import { AdaptersPage } from "./pages/AdaptersPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { ChatPage } from "./pages/ChatPage";
import { EventsPage } from "./pages/EventsPage";
import { LogsPage } from "./pages/LogsPage";
import { MemoryPage } from "./pages/MemoryPage";
import { ModelsPage } from "./pages/ModelsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PluginsPage } from "./pages/PluginsPage";
import { RoutinePage } from "./pages/RoutinePage";
import { SettingsPage } from "./pages/SettingsPage";
import { SubagentsPage } from "./pages/SubagentsPage";
import type { Action } from "./pages/common";

const TOKEN_KEY = "shiritaibot-token";

export function App() {
  return (
    <NoticeProvider>
      <ConsoleApp />
    </NoticeProvider>
  );
}

function ConsoleApp() {
  const [linkToken, setLinkToken] = useState(() =>
    location.pathname === "/token"
      ? new URLSearchParams(location.hash.slice(1)).get("token")
      : null,
  );
  const [token, setToken] = useState<string | null>(() =>
    linkToken ? null : sessionStorage.getItem(TOKEN_KEY),
  );
  const [page, navigate] = useHashRoute();
  const { succeed, fail, dismiss } = useNotice();
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const queryClient = useQueryClient();

  const authenticate = useCallback(
    (next: string) => {
      dismiss();
      sessionStorage.setItem(TOKEN_KEY, next);
      setToken(next);
    },
    [dismiss],
  );

  useLayoutEffect(() => {
    if (location.pathname === "/token") {
      history.replaceState(history.state, "", "/");
      if (linkToken) sessionStorage.removeItem(TOKEN_KEY);
    }
  }, [linkToken]);

  useEffect(() => {
    if (!linkToken) return;
    let active = true;
    void createClient(() => linkToken)
      .health()
      .then(() => {
        if (active) authenticate(linkToken);
      })
      .catch((error: unknown) => {
        if (active)
          fail(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (active) setLinkToken(null);
      });
    return () => {
      active = false;
    };
  }, [linkToken, authenticate, fail]);

  const client: ApiClient | null = useMemo(
    () => (token ? createClient(() => token) : null),
    [token],
  );
  const draft = useConfigDraft(client);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => client!.health(),
    enabled: client !== null,
    refetchInterval: 10000,
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client!.records("approval"),
    enabled: client !== null,
    refetchInterval: 4000,
  });

  // Surface transport/auth failures instead of silently showing empty pages.
  const queryError =
    draft.error ??
    (health.error instanceof Error ? health.error : null) ??
    (client && health.isError ? new Error("无法连接后端服务") : null);
  useEffect(() => {
    if (!queryError) return;
    if (queryError.message.includes("管理令牌")) {
      sessionStorage.removeItem(TOKEN_KEY);
      setToken(null);
    }
    fail(queryError.message);
  }, [queryError, fail]);

  const onRealtime = useCallback(
    (event: RealtimeEvent) => {
      void queryClient.invalidateQueries({ queryKey: ["health"] });
      void queryClient.invalidateQueries({ queryKey: ["records"] });
      if (event.kind === "turn" || event.kind === "reply") {
        void queryClient.invalidateQueries({ queryKey: ["history"] });
      }
    },
    [queryClient],
  );
  useRealtime(token, onRealtime);

  const pending = (approvals.data ?? []).filter(
    (row) => row.status === "pending" && row.reviewer !== "model",
  ).length;

  const act: Action = useCallback(
    async (work, successText) => {
      setBusy(true);
      try {
        const result = await work();
        if (successText !== "") {
          succeed(
            successText ??
              (result &&
              typeof result === "object" &&
              "text" in result &&
              typeof (result as { text?: unknown }).text === "string"
                ? String((result as { text: string }).text).slice(0, 500)
                : "操作已完成"),
          );
        }
        await queryClient.invalidateQueries();
      } catch (error) {
        fail(error instanceof Error ? error.message : String(error));
      } finally {
        setBusy(false);
      }
    },
    [queryClient, succeed, fail],
  );

  async function save() {
    setSaving(true);
    try {
      await draft.save();
      succeed("配置已保存并应用");
      await queryClient.invalidateQueries({ queryKey: ["health"] });
    } catch (error) {
      fail(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  if (!client) {
    return (
      <LoginView
        authenticating={Boolean(linkToken)}
        onAuthenticated={authenticate}
        onError={fail}
      />
    );
  }

  function content(api: ApiClient) {
    if (!draft.config) {
      return draft.loading ? <p className="muted">正在读取配置…</p> : null;
    }
    const configProps = {
      client: api,
      act,
      config: draft.config,
      setConfig: draft.update,
    };
    switch (page) {
      case "overview":
        return <OverviewPage client={api} act={act} onNavigate={navigate} />;
      case "chat":
        return (
          <ChatPage
            client={api}
            act={act}
            assistantName={draft.config.assistant_name}
          />
        );
      case "events":
        return <EventsPage client={api} act={act} />;
      case "routine":
        return <RoutinePage client={api} act={act} />;
      case "memory":
        return <MemoryPage client={api} act={act} />;
      case "logs":
        return <LogsPage client={api} act={act} />;
      case "models":
        return <ModelsPage {...configProps} />;
      case "adapters":
        return <AdaptersPage {...configProps} />;
      case "subagents":
        return <SubagentsPage {...configProps} />;
      case "plugins":
        return <PluginsPage {...configProps} />;
      case "approvals":
        return <ApprovalsPage {...configProps} />;
      case "settings":
        return <SettingsPage {...configProps} />;
      default:
        return null;
    }
  }

  return (
    <AppShell
      page={page}
      onNavigate={navigate}
      pendingApprovals={pending}
      online={health.isSuccess}
      version={"v" + (health.data?.version ?? "0.1.0")}
      onLogout={() => {
        dismiss();
        sessionStorage.removeItem(TOKEN_KEY);
        setToken(null);
        queryClient.clear();
      }}
    >
      {busy && <span className="sr-only">正在执行操作</span>}
      {content(client)}
      {draft.dirty && (
        <SaveBar
          busy={saving}
          onSave={() => void save()}
          onDiscard={draft.discard}
        />
      )}
    </AppShell>
  );
}
