import { ShareAltOutlined, LinkOutlined } from "@ant-design/icons";
import { Alert, Spin } from "antd";
import { useEffect, useState } from "react";
import { getSharedSession } from "../lib/api";
import { MarkdownRenderer } from "./MarkdownRenderer";
import type { SharedSessionResponse } from "../types";

function extractShareToken(): string | null {
  const match = window.location.pathname.match(/\/shared\/([A-Za-z0-9_-]+)/);
  return match ? match[1] : null;
}

export default function SharedSessionView() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<SharedSessionResponse | null>(null);

  const token = extractShareToken();

  useEffect(() => {
    if (!token) {
      setError("无效的分享链接");
      setLoading(false);
      return;
    }

    let cancelled = false;
    getSharedSession(token)
      .then((data) => {
        if (!cancelled) {
          setSession(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载失败");
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  if (loading) {
    return (
      <div
        style={{
          minHeight: "100dvh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--bg-page, #f8f6f2)",
        }}
      >
        <Spin size="large" tip="加载分享内容..." />
      </div>
    );
  }

  if (error || !session) {
    return (
      <div
        style={{
          minHeight: "100dvh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          background: "var(--bg-page, #f8f6f2)",
        }}
      >
        <Alert
          type="error"
          showIcon
          message="无法加载分享内容"
          description={error || "分享内容不存在或已被删除"}
        />
      </div>
    );
  }

  return (
    <div className="shared-session-page">
      <header className="shared-session-header">
        <div className="shared-header-inner">
          <div>
            <ShareAltOutlined style={{ marginRight: 8, opacity: 0.6 }} />
            <span className="shared-label">分享的会话</span>
          </div>
          <h1 className="shared-title">{session.title || "未命名会话"}</h1>
          <div className="shared-meta">
            {session.started_at && (
              <span>开始于 {new Date(session.started_at).toLocaleString("zh-CN")}</span>
            )}
            <span className="shared-status">
              {session.status === "completed" ? "已完成" : session.status}
            </span>
          </div>
        </div>
      </header>

      <main className="shared-messages">
        {session.messages.length === 0 ? (
          <Alert message="该会话暂无消息内容" type="info" showIcon />
        ) : (
          session.messages.map((msg, idx) => (
            <div
              key={idx}
              className={`shared-message shared-message--${msg.role}`}
            >
              <div className="shared-message-role">
                {msg.role === "user" ? "用户" : "助手"}
              </div>
              <div className="shared-message-content">
                {msg.content ? (
                  <MarkdownRenderer content={msg.content} />
                ) : (
                  <span style={{ opacity: 0.4 }}>(空内容)</span>
                )}
              </div>
            </div>
          ))
        )}
      </main>

      <footer className="shared-footer">
        <LinkOutlined style={{ marginRight: 6 }} />
        此内容由 DeepAgents 分享 · 只读视图
      </footer>
    </div>
  );
}
