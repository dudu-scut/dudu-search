import {
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  FilterOutlined,
  StopOutlined,
  ToolOutlined
} from "@ant-design/icons";
import { Empty, Space, Tag } from "antd";
import { useMemo, useState } from "react";
import type { MonitorMessage } from "../types";

interface EventFilter {
  event: string;
  label: string;
}

const EVENT_FILTERS: EventFilter[] = [
  { event: "tool_start", label: "🔧 工具调用" },
  { event: "assistant_call", label: "🤖 助手调用" },
  { event: "session_created", label: "📋 会话创建" },
  { event: "task_result", label: "✅ 任务结果" },
  { event: "task_cancelled", label: "❌ 任务取消" },
  { event: "error", label: "⚠️ 异常" },
];

const ALL_EVENTS = EVENT_FILTERS.map((f) => f.event);

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}

function EventIcon({ event }: { event: string }) {
  if (event === "assistant_call") {
    return <BranchesOutlined aria-hidden />;
  }
  if (event === "tool_start") {
    return <ToolOutlined aria-hidden />;
  }
  if (event === "session_created") {
    return <FileSearchOutlined aria-hidden />;
  }
  if (event === "task_result") {
    return <CheckCircleOutlined aria-hidden />;
  }
  if (event === "task_cancelled") {
    return <StopOutlined aria-hidden />;
  }
  if (event === "error") {
    return <CloseCircleOutlined aria-hidden />;
  }
  return <ClockCircleOutlined aria-hidden />;
}

interface EventStreamProps {
  events: MonitorMessage[];
}

export function EventStream({ events }: EventStreamProps) {
  const [activeFilters, setActiveFilters] = useState<string[]>([]);

  const hasActiveFilters = activeFilters.length > 0;

  const filteredEvents = useMemo(() => {
    if (!hasActiveFilters) return events;
    return events.filter((event) => activeFilters.includes(event.event));
  }, [events, activeFilters, hasActiveFilters]);

  function toggleFilter(event: string) {
    setActiveFilters((prev) =>
      prev.includes(event)
        ? prev.filter((f) => f !== event)
        : [...prev, event]
    );
  }

  function clearFilters() {
    setActiveFilters([]);
  }

  function selectAllFilters() {
    setActiveFilters([...ALL_EVENTS]);
  }

  return (
    <section className="console-panel event-panel" aria-labelledby="event-title">
      <div className="panel-heading">
        <div>
          <span className="panel-kicker">LIVE TRACE</span>
          <h2 id="event-title">实时执行轨迹</h2>
        </div>
        <span className="event-count">{filteredEvents.length}</span>
      </div>

      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--line)" }}>
        <Space size={[4, 4]} wrap>
          <FilterOutlined style={{ color: "var(--muted)", marginRight: 4 }} />
          {EVENT_FILTERS.map((f) => (
            <Tag.CheckableTag
              checked={activeFilters.includes(f.event)}
              key={f.event}
              onChange={() => toggleFilter(f.event)}
              style={{
                cursor: "pointer",
                fontSize: 12,
                padding: "2px 10px",
                borderRadius: 6,
              }}
            >
              {f.label}
            </Tag.CheckableTag>
          ))}
          {!hasActiveFilters ? (
            <Tag
              style={{ cursor: "pointer", fontSize: 12 }}
              onClick={selectAllFilters}
            >
              全选
            </Tag>
          ) : (
            <Tag
              color="default"
              onClick={clearFilters}
              style={{ cursor: "pointer", fontSize: 12 }}
            >
              清除筛选
            </Tag>
          )}
        </Space>
      </div>

      {filteredEvents.length === 0 ? (
        <div className="empty-console">
          <Empty
            description={hasActiveFilters ? "当前筛选条件下无事件" : "等待 WebSocket 推送任务事件"}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      ) : (
        <ol className="event-stream">
          {filteredEvents.map((event, index) => (
            <li className={`event-row event-row--${event.event}`} key={`${event.timestamp}-${index}`}>
              <div className="event-icon">
                <EventIcon event={event.event} />
              </div>
              <div className="event-body">
                <div className="event-meta">
                  <span>{event.event}</span>
                  <time dateTime={event.timestamp}>{formatTime(event.timestamp)}</time>
                </div>
                <p>{event.message}</p>
                {Object.keys(event.data).length > 0 ? (
                  <pre>{JSON.stringify(event.data, null, 2)}</pre>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
