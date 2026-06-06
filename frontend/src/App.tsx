import {
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  LogoutOutlined,
  ToolOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Alert, App as AntApp, Avatar, Button, Dropdown, Space } from "antd";
import { useEffect, useRef, useState } from "react";
import { ChatComposer } from "./components/ChatComposer";
import { ConversationThread } from "./components/ConversationThread";
import LoginPage from "./components/LoginPage";
import MemoryPanel from "./components/MemoryPanel";
import SessionList from "./components/SessionList";
import TaskHistory from "./components/TaskHistory";
import type { ChatTurn } from "./components/ConversationThread";
import { API_BASE_URL, WS_BASE_URL } from "./lib/config";
import { getUser, isLoggedIn, logout, handleSSOCallback } from "./lib/auth";
import { useDeepAgentSession } from "./hooks/useDeepAgentSession";
import type { ConnectionState, UploadedItem } from "./types";

function connectionLabel(state: ConnectionState): string {
  const labels: Record<ConnectionState, string> = {
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "重连中",
    closed: "已关闭"
  };
  return labels[state];
}

function createTurn(content: string): ChatTurn {
  return {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`,
    content,
    events: [],
    files: [],
    isRunning: true,
    result: "",
    timestamp: new Date().toISOString()
  };
}

function AuthenticatedApp() {
  const { message } = AntApp.useApp();
  const [query, setQuery] = useState("");
  const [stagedItems, setStagedItems] = useState<UploadedItem[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const streamRef = useRef<HTMLElement | null>(null);
  const session = useDeepAgentSession();

  useEffect(() => {
    if (session.isViewingHistory) return;
    setTurns((previous) => {
      if (previous.length === 0) {
        return previous;
      }

      const latestTurn = previous[previous.length - 1];
      const nextLatestTurn = {
        ...latestTurn,
        events: session.events,
        files: session.files,
        isRunning: session.isRunning,
        result: session.result
      };

      return [...previous.slice(0, -1), nextLatestTurn];
    });
  }, [session.events, session.files, session.isRunning, session.result, session.isViewingHistory]);

  useEffect(() => {
    const streamNode = streamRef.current;
    if (!streamNode) {
      return;
    }

    window.requestAnimationFrame(() => {
      // 只在用户已接近底部（80px 以内）时自动滚动，否则保持用户当前阅读位置
      const threshold = 80;
      const distanceFromBottom =
        streamNode.scrollHeight - streamNode.scrollTop - streamNode.clientHeight;
      if (distanceFromBottom < threshold) {
        streamNode.scrollTo({
          top: streamNode.scrollHeight,
          behavior: "smooth",
        });
      }
    });
  }, [turns]);

  async function handleSubmit() {
    const cleanQuery = query.trim();
    if (!cleanQuery) {
      message.warning("请输入研搜任务");
      return;
    }

    // 在历史会话中提交新任务时，清除查看标记但不重置 threadId，
    // 确保 useEffect 能正常同步事件到最新 turn，且任务提交到正确的历史会话
    if (session.isViewingHistory) {
      session.clearHistoryView();
    }

    const nextTurn = createTurn(cleanQuery);
    setTurns((previous) => [...previous, nextTurn]);
    setQuery("");

    try {
      await session.submitTask(cleanQuery);
      message.success("任务已启动，执行过程会显示在对话中");
    } catch (error) {
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === nextTurn.id
            ? {
                ...turn,
                isRunning: false,
                result: error instanceof Error ? error.message : "任务启动失败"
              }
            : turn
        )
      );
      message.error(error instanceof Error ? error.message : "任务启动失败");
    }
  }

  async function handleCancel() {
    try {
      const response = await session.cancelCurrentTask();
      message.info(response.status === "cancelling" ? "取消请求已发送，正在等待当前调用结束" : "任务已取消");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  async function handleUpload(items: UploadedItem[]) {
    try {
      const response = await session.uploadFiles(items);
      setStagedItems([]);
      message.success(`已上传 ${response.files.length} 个文件`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "上传失败");
    }
  }

  async function handleRemoveFile(itemName: string) {
    try {
      await session.removeUploadedFile(itemName);
      message.success(`已删除 ${itemName}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "删除文件失败");
    }
  }

  function handleNewSession() {
    if (session.isViewingHistory) {
      session.exitHistoryView();
    } else {
      session.resetSession();
    }
    setTurns([]);
    setQuery("");
    setStagedItems([]);
  }

  async function handleSelectSession(threadId: string) {
    if (session.isViewingHistory && session.viewingSessionId === threadId) {
      return; // Already viewing this session
    }

    try {
      const { detail, historyEvents } = await session.loadHistoricalSession(threadId);

      // 从历史消息重建 ChatTurn（使用返回值中的 historyEvents 避免 React setState 异步导致的值滞后）
      const userMessages = (detail.messages || []).filter((m) => m.role === "user");
      const assistantMessages = (detail.messages || []).filter((m) => m.role === "assistant");

      if (userMessages.length > 0) {
        const turns: ChatTurn[] = userMessages.map((userMsg, index) => {
          const assistantMsg = assistantMessages[index];
          return {
            id: `${threadId}-${index}`,
            content: userMsg.content || "",
            events: index === userMessages.length - 1 ? historyEvents : [],
            files: [],
            isRunning: detail.status === "running",
            result: assistantMsg?.content || "",
            timestamp: userMsg.created_at,
          };
        });
        setTurns(turns);
      } else {
        // 兼容旧数据：无用户消息时仍然显示 events
        setTurns([
          {
            id: threadId,
            content: detail.title || "(无标题)",
            events: historyEvents,
            files: [],
            isRunning: detail.status === "running",
            result: assistantMessages[assistantMessages.length - 1]?.content || "",
            timestamp: detail.started_at || new Date().toISOString(),
          },
        ]);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "加载历史会话失败");
    }
  }

  const online = session.connectionState === "connected";

  return (
    <div className="chat-app-shell min-h-dvh">
      <aside className="chat-sidebar" aria-label="会话信息">
        <div className="sidebar-brand">
          <span className="panel-kicker">DEEPSEARCH</span>
          <h1>深度研搜</h1>
          <p>对话式多智能体研究台</p>
        </div>

        <Button className="new-chat-button" block onClick={handleNewSession}>
          新建研搜
        </Button>

        <SessionList
          activeThreadId={session.threadId}
          onSelect={(threadId: string) => {
            handleSelectSession(threadId);
          }}
          onNewSession={handleNewSession}
        />

        <MemoryPanel />

        <div className="sidebar-section">
          <span className="sidebar-label">THREAD</span>
          <strong className="thread-id" title={session.threadId}>
            {session.threadId.slice(0, 8)}
          </strong>
        </div>

        <div className="sidebar-status-list">
          <div className={`sidebar-status ${online ? "sidebar-status--online" : "sidebar-status--warn"}`}>
            <ApiOutlined aria-hidden />
            <span>WebSocket</span>
            <strong>{connectionLabel(session.connectionState)}</strong>
          </div>
          <div className="sidebar-status">
            <BranchesOutlined aria-hidden />
            <span>助手调度</span>
            <strong>{session.stats.assistantEvents}</strong>
          </div>
          <div className="sidebar-status">
            <ToolOutlined aria-hidden />
            <span>工具调用</span>
            <strong>{session.stats.toolEvents}</strong>
          </div>
          <div className={session.stats.errorEvents > 0 ? "sidebar-status sidebar-status--error" : "sidebar-status"}>
            <CloseCircleOutlined aria-hidden />
            <span>异常</span>
            <strong>{session.stats.errorEvents}</strong>
          </div>
        </div>

        <div className="sidebar-section">
          <span className="sidebar-label">AGENTS</span>
          <ul className="agent-mini-list">
            <li>
              <CloudServerOutlined aria-hidden />
              网络搜索助手
            </li>
            <li>
              <DatabaseOutlined aria-hidden />
              数据库查询助手
            </li>
            <li>
              <FileSearchOutlined aria-hidden />
              RAGFlow 助手
            </li>
          </ul>
        </div>

        <div className="sidebar-section sidebar-endpoints">
          <span className="sidebar-label">ENDPOINTS</span>
          <code>{API_BASE_URL}</code>
          <code>{WS_BASE_URL}</code>
        </div>
      </aside>

      <main className="chat-main">
        <header className="chat-topbar">
          <div>
            <span className="panel-kicker">CHAT WORKSPACE</span>
            <h2>深度研搜对话</h2>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Button
              icon={<HistoryOutlined />}
              onClick={() => setShowHistory((prev) => !prev)}
              type={showHistory ? "primary" : "default"}
            >
              历史
            </Button>
            <div className={`run-indicator ${session.isRunning ? "run-indicator--live" : ""}`}>
              {session.isRunning ? <BranchesOutlined aria-hidden /> : <CheckCircleOutlined aria-hidden />}
              {session.isRunning ? "研搜中" : "待命"}
            </div>
            <Dropdown
              menu={{
                items: [
                  {
                    key: "user",
                    label: getUser()?.username || "用户",
                    disabled: true,
                  },
                  {
                    type: "divider",
                  },
                  {
                    key: "logout",
                    icon: <LogoutOutlined />,
                    label: "退出登录",
                    danger: true,
                    onClick: logout,
                  },
                ],
              }}
              placement="bottomRight"
            >
              <Button type="text" style={{ display: "flex", alignItems: "center" }}>
                <Space>
                  <Avatar size={24} icon={<UserOutlined />} />
                  <span>{getUser()?.username || "用户"}</span>
                </Space>
              </Button>
            </Dropdown>
          </div>
        </header>

        {session.lastError ? (
          <Alert
            className="chat-alert"
            message={session.lastError}
            showIcon
            type="error"
          />
        ) : null}

        <TaskHistory
          activeThreadId={session.threadId}
          onSelect={(threadId: string) => {
            handleSelectSession(threadId);
          }}
          visible={showHistory}
          onClose={() => setShowHistory(false)}
        />

        <section className="chat-stream-panel" ref={streamRef}>
          {session.isViewingHistory && (
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 16px",
                background: "#fff7e6",
                borderBottom: "1px solid #ffd591",
                marginBottom: 8,
              }}
            >
              <span>
                📖 正在查看历史会话：<strong>{session.viewingSessionTitle}</strong>
                {session.isLoadingHistory ? " (加载中...)" : ""}
              </span>
              <Space>
                <Button size="small" onClick={handleNewSession} type="primary">
                  新建研搜
                </Button>
              </Space>
            </div>
          )}
          <ConversationThread
            onUseExample={setQuery}
            turns={turns}
          />
        </section>

        <ChatComposer
          isCancelling={session.isCancelling}
          isRunning={session.isRunning}
          isUploading={session.isUploading}
          onCancel={handleCancel}
          onNewSession={handleNewSession}
          onQueryChange={setQuery}
          onRemoveFile={handleRemoveFile}
          onStagedItemsChange={setStagedItems}
          onSubmit={handleSubmit}
          onUpload={handleUpload}
          query={query}
          stagedItems={stagedItems}
          uploadedItems={session.uploadedItems}
        />
      </main>
    </div>
  );
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(() => {
    if (handleSSOCallback()) return true;
    return isLoggedIn();
  });

  if (!loggedIn) {
    return <LoginPage onLoginSuccess={() => setLoggedIn(true)} />;
  }

  return <AuthenticatedApp />;
}
