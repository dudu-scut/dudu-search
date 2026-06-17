import { useCallback, useEffect, useRef } from "react";
import { getToken } from "../lib/auth";
import { API_BASE_URL } from "../lib/config";

type EventHandler = (data: unknown) => void;

/**
 * SSE 实时事件推送 Hook — 替代 HTTP 轮询。
 *
 * 连接后端 /api/events/stream 端点，监听 session_updated 和 files_updated 事件。
 * 内置自动重连（3 秒延迟），组件卸载时清理 EventSource。
 *
 * @param threadId - 当前会话 ID，用于订阅文件变更通知
 * @param handlers - 事件处理函数映射
 */
export function useSSE(
  threadId: string,
  handlers: {
    onSessionUpdated?: EventHandler;
    onFilesUpdated?: EventHandler;
  }
) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let disposed = false;

    function connect() {
      // 清理旧连接
      esRef.current?.close();
      if (reconnectTimerRef.current) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = undefined;
      }

      const token = getToken();
      if (!token || disposed) {
        return;
      }

      const baseUrl = API_BASE_URL || window.location.origin;
      const params = new URLSearchParams({ token });
      if (threadId) {
        params.set("thread_id", threadId);
      }

      const url = `${baseUrl}/api/events/stream?${params}`;
      const es = new EventSource(url);
      esRef.current = es;

      es.addEventListener("session_updated", (event: MessageEvent) => {
        try {
          handlersRef.current.onSessionUpdated?.(JSON.parse(event.data));
        } catch {
          // 忽略解析错误
        }
      });

      es.addEventListener("files_updated", (event: MessageEvent) => {
        try {
          handlersRef.current.onFilesUpdated?.(JSON.parse(event.data));
        } catch {
          // 忽略解析错误
        }
      });

      es.onerror = () => {
        es.close();
        if (disposed) {
          return;
        }
        // 3 秒后重连
        reconnectTimerRef.current = window.setTimeout(connect, 3000);
      };
    }

    connect();

    return () => {
      disposed = true;
      esRef.current?.close();
      if (reconnectTimerRef.current) {
        window.clearTimeout(reconnectTimerRef.current);
      }
    };
  }, [threadId]);
}
