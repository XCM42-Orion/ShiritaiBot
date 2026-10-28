import { useEffect } from "react";

export interface RealtimeEvent {
  kind: string;
  id?: string;
}

const MAX_RETRY_DELAY = 30000;

/**
 * Subscribes to the backend WebSocket. Reconnects with exponential backoff and
 * ignores heartbeats so callers only see meaningful state changes.
 */
export function useRealtime(
  token: string | null,
  onEvent: (event: RealtimeEvent) => void,
) {
  useEffect(() => {
    if (!token) return;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let attempt = 0;

    const connect = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/api/ws`);
      socket.onopen = () => {
        attempt = 0;
        socket?.send(token);
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as RealtimeEvent;
          if (event.kind !== "heartbeat") onEvent(event);
        } catch {
          // Malformed frame: ignore and wait for the next event.
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        const delay = Math.min(MAX_RETRY_DELAY, 1000 * 2 ** attempt++);
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [token, onEvent]);
}
