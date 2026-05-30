import {
  DeleteOutlined,
  HistoryOutlined,
  LoadingOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, List, Popconfirm, Spin, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { deleteSession, listSessions } from "../lib/api";
import type { SessionSummary } from "../types";

const { Text } = Typography;

interface Props {
  activeThreadId: string;
  onSelect: (threadId: string) => void;
  onNewSession: () => void;
}

export default function SessionList({ activeThreadId, onSelect, onNewSession }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listSessions();
      setSessions(res.sessions);
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
    const timer = setInterval(loadSessions, 30000);
    return () => clearInterval(timer);
  }, [loadSessions]);

  async function handleDelete(threadId: string) {
    await deleteSession(threadId);
    setSessions((prev) => prev.filter((s) => s.thread_id !== threadId));
  }

  const statusColor: Record<string, string> = {
    running: "processing",
    completed: "success",
    cancelled: "warning",
    error: "error",
  };

  return (
    <div style={{ padding: "8px 0" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0 12px 8px",
        }}
      >
        <Text strong style={{ fontSize: 12, color: "#8c8c8c" }}>
          <HistoryOutlined /> 历史会话
        </Text>
        <Button
          type="text"
          size="small"
          icon={<PlusOutlined />}
          onClick={onNewSession}
        />
      </div>
      {loading && sessions.length === 0 ? (
        <div style={{ textAlign: "center", padding: 16 }}>
          <Spin indicator={<LoadingOutlined />} size="small" />
        </div>
      ) : (
        <List
          size="small"
          dataSource={sessions}
          locale={{ emptyText: "暂无历史会话" }}
          renderItem={(item) => (
            <List.Item
              style={{
                padding: "6px 12px",
                cursor: "pointer",
                background:
                  item.thread_id === activeThreadId ? "#e6f4ff" : undefined,
              }}
              onClick={() => onSelect(item.thread_id)}
              actions={[
                <Popconfirm
                  key="delete"
                  title="确认删除此会话？"
                  onConfirm={(e) => {
                    e?.stopPropagation();
                    handleDelete(item.thread_id);
                  }}
                  onCancel={(e) => e?.stopPropagation()}
                >
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={(e) => e.stopPropagation()}
                  />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Text
                    ellipsis
                    style={{
                      fontSize: 13,
                      maxWidth: 160,
                      fontWeight:
                        item.thread_id === activeThreadId ? 600 : 400,
                    }}
                  >
                    {item.title || item.thread_id.slice(0, 8)}
                  </Text>
                }
                description={
                  <span style={{ fontSize: 11 }}>
                    <Tag
                      color={statusColor[item.status] || "default"}
                      style={{ fontSize: 10, lineHeight: "16px" }}
                    >
                      {item.status}
                    </Tag>
                    {item.message_count} 条消息
                  </span>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
}
