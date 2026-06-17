import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cancelTask, deleteUploadedFile, getSessionDetail, listSessionFiles, startTask, uploadSessionFiles } from "../lib/api";
import { getToken } from "../lib/auth";
import { WS_BASE_URL } from "../lib/config";
import { createThreadId, getStoredThreadId, storeThreadId } from "../lib/thread";
import type {
  ConnectionState,
  MonitorMessage,
  OutputFile,
  SocketMessage,
  UploadedItem
} from "../types";

const MAX_EVENTS = 120;

function extractString(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  return typeof value === "string" ? value : null;
}

export function useDeepAgentSession() {
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const heartbeatTimerRef = useRef<number | undefined>(undefined);
  const uploadedNameSetRef = useRef<Set<string>>(new Set());
  const [threadId, setThreadId] = useState(getStoredThreadId);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [events, setEvents] = useState<MonitorMessage[]>([]);
  const [files, setFiles] = useState<OutputFile[]>([]);
  const [sessionPath, setSessionPath] = useState("");
  const [result, setResult] = useState("");
  const [lastError, setLastError] = useState("");
  const [lastPongAt, setLastPongAt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedItems, setUploadedItems] = useState<UploadedItem[]>([]);
  const [isViewingHistory, setIsViewingHistory] = useState(false);
  const [viewingSessionId, setViewingSessionId] = useState<string | null>(null);
  const [viewingSessionTitle, setViewingSessionTitle] = useState("");
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  const clearSocketTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = undefined;
    }
  }, []);

  const resetSession = useCallback(() => {
    const nextThreadId = createThreadId();
    storeThreadId(nextThreadId);
    setThreadId(nextThreadId);
    setEvents([]);
    setFiles([]);
    setSessionPath("");
    setResult("");
    setLastError("");
    setUploadedItems([]);
    uploadedNameSetRef.current.clear();
    setIsRunning(false);
    setIsCancelling(false);
  }, []);

  const refreshFiles = useCallback(async () => {
    if (!sessionPath) {
      return;
    }

    const response = await listSessionFiles(sessionPath);
    if (response.error) {
      throw new Error(response.error);
    }
    // 过滤掉用户上传的文件，只保留 Agent 生成的输出文件
    const outputFiles = (response.files || []).filter(
      (file) => !uploadedNameSetRef.current.has(file.name)
    );
    setFiles(outputFiles);
  }, [sessionPath]);

  useEffect(() => {
    let disposed = false;

    function connect() {
      clearSocketTimers();
      const hadSocket = Boolean(socketRef.current);
      socketRef.current?.close();
      setConnectionState(hadSocket ? "reconnecting" : "connecting");

      const socket = new WebSocket(`${WS_BASE_URL}/ws/${encodeURIComponent(threadId)}?token=${encodeURIComponent(getToken() || "")}`);
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) {
          return;
        }
        setConnectionState("connected");
        setLastError("");
        heartbeatTimerRef.current = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 25000);
      };

      socket.onmessage = (event) => {
        if (socketRef.current !== socket) {
          return;
        }
        // 服务端心跳回复是纯文本 "pong"，先处理再解析 JSON
        if (event.data === "pong") {
          setLastPongAt(new Date().toISOString());
          return;
        }
        try {
          const payload = JSON.parse(event.data) as SocketMessage;

          if (payload.type !== "monitor_event") {
            return;
          }

          setEvents((previous) => [...previous, payload].slice(-MAX_EVENTS));

          if (payload.event === "session_created") {
            const path = extractString(payload.data, "path");
            if (path) {
              setSessionPath(path);
            }
          }

          if (payload.event === "streaming_content") {
            const partialContent = extractString(payload.data, "content");
            if (partialContent) {
              setResult((previous) => previous + partialContent);
            }
          }

          if (payload.event === "task_result") {
            const finalResult = extractString(payload.data, "result");
            if (finalResult) {
              setResult(finalResult);
            }
            setIsRunning(false);
            setIsCancelling(false);
          }

          if (payload.event === "task_cancelled") {
            setResult((previous) => previous || payload.message);
            setIsRunning(false);
            setIsCancelling(false);
          }

          if (payload.event === "error") {
            setLastError(payload.message);
            setResult((previous) => previous || `❌ ${payload.message}`);
            setIsRunning(false);
            setIsCancelling(false);
          }
        } catch (error) {
          setLastError(error instanceof Error ? error.message : "WebSocket 消息解析失败");
        }
      };

      socket.onerror = () => {
        if (!disposed && socketRef.current === socket) {
          setLastError("WebSocket 连接异常，请确认后端服务已启动");
        }
      };

      socket.onclose = () => {
        if (socketRef.current !== socket) {
          return;
        }
        clearSocketTimers();
        if (disposed) {
          setConnectionState("closed");
          return;
        }
        setConnectionState("reconnecting");
        reconnectTimerRef.current = window.setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      disposed = true;
      clearSocketTimers();
      socketRef.current?.close();
    };
  }, [clearSocketTimers, threadId]);

  useEffect(() => {
    if (!sessionPath) {
      return;
    }

    refreshFiles().catch((error: unknown) => {
      setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
    });

    const timer = window.setInterval(() => {
      refreshFiles().catch((error: unknown) => {
        setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
      });
    }, isRunning ? 15000 : 30000);

    return () => window.clearInterval(timer);
  }, [isRunning, refreshFiles, sessionPath]);

  const submitTask = useCallback(
    async (query: string) => {
      const cleanQuery = query.trim();
      if (!cleanQuery) {
        throw new Error("请输入研搜任务");
      }

      setIsRunning(true);
      setIsCancelling(false);
      setEvents([]);
      setResult("");
      setLastError("");
      try {
        const response = await startTask(cleanQuery, threadId);
        if (response.thread_id && response.thread_id !== threadId) {
          storeThreadId(response.thread_id);
          setThreadId(response.thread_id);
        }
        return response;
      } catch (error) {
        setIsRunning(false);
        setIsCancelling(false);
        throw error;
      }
    },
    [threadId]
  );

  const cancelCurrentTask = useCallback(async () => {
    if (!isRunning) {
      throw new Error("当前没有正在执行的任务");
    }

    setIsCancelling(true);
    setLastError("");
    try {
      const response = await cancelTask(threadId);
      if (response.status === "cancelled") {
        setIsRunning(false);
        setIsCancelling(false);
        setResult((previous) => previous || "任务已取消");
      }
      return response;
    } catch (error) {
      setIsCancelling(false);
      throw error;
    }
  }, [isRunning, threadId]);

  const uploadFiles = useCallback(
    async (items: UploadedItem[]) => {
      if (items.length === 0) {
        throw new Error("请选择要上传的文件");
      }

      const nextItems = items.filter((item) => !uploadedNameSetRef.current.has(item.name));

      if (nextItems.length === 0) {
        return {
          status: "uploaded",
          files: Array.from(uploadedNameSetRef.current)
        };
      }

      setIsUploading(true);
      setLastError("");
      try {
        const response = await uploadSessionFiles(
          nextItems.map((item) => item.raw),
          threadId
        );
        setUploadedItems((previous) => {
          const names = new Set(previous.map((item) => item.name));
          const next = [...previous];
          nextItems.forEach((item) => {
            if (!names.has(item.name)) {
              names.add(item.name);
              uploadedNameSetRef.current.add(item.name);
              next.push(item);
            }
          });
          return next;
        });
        return response;
      } finally {
        setIsUploading(false);
      }
    },
    [threadId]
  );

  const removeUploadedFile = useCallback(
    async (itemName: string) => {
      setLastError("");
      try {
        await deleteUploadedFile(threadId, itemName);
      } catch (error) {
        // 后端删除失败也不阻塞前端 UI 移除
        setLastError(error instanceof Error ? error.message : "文件删除失败");
      }

      // 从 uploadedItems 和 name set 中移除
      setUploadedItems((previous) =>
        previous.filter((item) => item.name !== itemName)
      );
      uploadedNameSetRef.current.delete(itemName);
    },
    [threadId]
  );

  const loadHistoricalSession = useCallback(
    async (threadId: string) => {
      setIsLoadingHistory(true);
      setLastError("");
      setIsCancelling(false);
      try {
        const detail = await getSessionDetail(threadId);

        // 将数据库中的 agent_events 转换为前端 MonitorMessage 格式
        const historyEvents: MonitorMessage[] = (detail.events || []).map((evt) => ({
          type: "monitor_event" as const,
          event: evt.event_type,
          message: evt.message,
          data: (evt.payload || {}) as Record<string, unknown>,
          timestamp: evt.created_at,
        }));

        // 将 threadId 切换为历史会话 ID，触发 WebSocket 重连到历史会话
        // submitTask 使用 threadId，因此后续提交会自动发到正确的会话
        storeThreadId(threadId);
        setThreadId(threadId);
        setEvents(historyEvents);
        setViewingSessionId(threadId);
        setViewingSessionTitle(detail.title || threadId.slice(0, 8));
        setIsViewingHistory(true);
        setIsRunning(detail.status === "running");
        setFiles([]);
        setSessionPath("");
        setResult("");

        return { detail, historyEvents };
      } catch (error) {
        const msg = error instanceof Error ? error.message : "加载历史会话失败";
        setLastError(msg);
        throw error;
      } finally {
        setIsLoadingHistory(false);
      }
    },
    []
  );

  // 软切换：清除历史查看标记但保留 threadId 和 events
  // 用于用户在历史会话中提交新任务时的模式过渡
  const clearHistoryView = useCallback(() => {
    setIsViewingHistory(false);
    setViewingSessionId(null);
    setViewingSessionTitle("");
    setIsLoadingHistory(false);
    // threadId 和 events 保持不变 — submitTask 即将使用当前 threadId
  }, []);

  const exitHistoryView = useCallback(() => {
    setIsViewingHistory(false);
    setViewingSessionId(null);
    setViewingSessionTitle("");
    setIsLoadingHistory(false);
    // 退出历史查看时重置为新的 live session
    resetSession();
  }, [resetSession]);

  const stats = useMemo(() => {
    const toolEvents = events.filter((event) => event.event === "tool_start").length;
    const assistantEvents = events.filter((event) => event.event === "assistant_call").length;
    const errorEvents = events.filter((event) => event.event === "error").length;

    return {
      toolEvents,
      assistantEvents,
      errorEvents,
      fileCount: files.length
    };
  }, [events, files.length]);

  return {
    connectionState,
    events,
    files,
    isCancelling,
    isLoadingHistory,
    isRunning,
    isUploading,
    isViewingHistory,
    lastError,
    lastPongAt,
    loadHistoricalSession,
    refreshFiles,
    resetSession,
    result,
    sessionPath,
    stats,
    cancelCurrentTask,
    clearHistoryView,
    exitHistoryView,
    removeUploadedFile,
    submitTask,
    threadId,
    uploadFiles,
    uploadedItems,
    viewingSessionId,
    viewingSessionTitle
  };
}
