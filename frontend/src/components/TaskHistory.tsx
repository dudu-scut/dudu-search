import {
  SearchOutlined,
  ReloadOutlined,
  HistoryOutlined,
} from "@ant-design/icons";
import {
  Input,
  Select,
  Table,
  Tag,
  Typography,
  Space,
  Button,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { listSessions } from "../lib/api";
import type { SessionSummary } from "../types";

const { Text } = Typography;

interface TaskHistoryProps {
  activeThreadId: string;
  onSelect: (threadId: string) => void;
  visible: boolean;
  onClose: () => void;
}

const STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
  error: "异常",
};

const STATUS_COLORS: Record<string, string> = {
  running: "processing",
  completed: "success",
  cancelled: "warning",
  error: "error",
};

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "running", label: "运行中" },
  { value: "completed", label: "已完成" },
  { value: "cancelled", label: "已取消" },
  { value: "error", label: "异常" },
];

function formatDateTime(value: string | null): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function TaskHistory({
  activeThreadId,
  onSelect,
  visible,
  onClose,
}: TaskHistoryProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listSessions(200, 0);
      setSessions(res.sessions || []);
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    loadSessions();
    const timer = setInterval(loadSessions, 30000);
    return () => clearInterval(timer);
  }, [visible, loadSessions]);

  const filteredSessions = useMemo(() => {
    return sessions.filter((session) => {
      const matchSearch =
        !searchText ||
        (session.title || session.thread_id)
          .toLowerCase()
          .includes(searchText.toLowerCase());
      const matchStatus = !statusFilter || session.status === statusFilter;
      return matchSearch && matchStatus;
    });
  }, [sessions, searchText, statusFilter]);

  const columns: ColumnsType<SessionSummary> = [
    {
      title: "标题",
      dataIndex: "title",
      key: "title",
      render: (title: string, record: SessionSummary) => (
        <Text ellipsis style={{ maxWidth: 320 }}>
          {title || record.thread_id.slice(0, 12)}
        </Text>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status] || "default"}>
          {STATUS_LABELS[status] || status}
        </Tag>
      ),
    },
    {
      title: "消息数",
      dataIndex: "message_count",
      key: "message_count",
      width: 80,
      align: "center",
    },
    {
      title: "创建时间",
      dataIndex: "started_at",
      key: "started_at",
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "完成时间",
      dataIndex: "completed_at",
      key: "completed_at",
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  if (!visible) return null;

  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 12,
        padding: 20,
        marginBottom: 16,
        boxShadow: "var(--shadow)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <Space>
          <HistoryOutlined />
          <Text strong style={{ fontSize: 16 }}>
            历史任务
          </Text>
          <Text type="secondary" style={{ fontSize: 13 }}>
            共 {filteredSessions.length} 条
          </Text>
        </Space>
        <Space>
          <Input
            allowClear
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="搜索标题..."
            prefix={<SearchOutlined />}
            style={{ width: 200 }}
            value={searchText}
          />
          <Select
            onChange={(value) => setStatusFilter(value)}
            options={STATUS_OPTIONS}
            style={{ width: 120 }}
            value={statusFilter}
          />
          <Button
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={loadSessions}
            type="text"
          />
          <Button onClick={onClose} type="text">
            关闭
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={filteredSessions}
        loading={loading}
        locale={{ emptyText: "暂无历史任务" }}
        onRow={(record) => ({
          onClick: () => onSelect(record.thread_id),
          style: {
            cursor: "pointer",
            background:
              record.thread_id === activeThreadId
                ? "var(--line)"
                : undefined,
          },
        })}
        pagination={{ pageSize: 15, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
        rowKey="thread_id"
        size="middle"
      />
    </div>
  );
}
