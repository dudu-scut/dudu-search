import { DeleteOutlined, BulbOutlined } from "@ant-design/icons";
import { Button, List, Popconfirm, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

const { Text } = Typography;

interface Memory {
  id: string;
  memory_type: string;
  content: string;
  importance: number;
  created_at: string;
}

const MEMORY_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  fact: { label: "事实", color: "blue" },
  preference: { label: "偏好", color: "green" },
  episodic: { label: "经历", color: "orange" },
  semantic: { label: "知识", color: "purple" },
};

async function fetchMemories(): Promise<Memory[]> {
  const res = await fetch("/api/memories?limit=100");
  if (!res.ok) return [];
  const data = await res.json();
  return data.memories || [];
}

async function deleteMemoryById(id: string): Promise<boolean> {
  const res = await fetch(`/api/memories/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  return res.ok;
}

export default function MemoryPanel() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setMemories(await fetchMemories());
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDelete(id: string) {
    if (await deleteMemoryById(id)) {
      setMemories((prev) => prev.filter((m) => m.id !== id));
    }
  }

  return (
    <div style={{ padding: "8px 0" }}>
      <div style={{ padding: "0 12px 8px" }}>
        <Text strong style={{ fontSize: 12, color: "#8c8c8c" }}>
          <BulbOutlined /> Agent 记忆 ({memories.length})
        </Text>
      </div>
      <List
        size="small"
        dataSource={memories}
        locale={{ emptyText: "暂无记忆，多和 Agent 对话后会自动生成" }}
        renderItem={(item) => {
          const typeInfo = MEMORY_TYPE_LABELS[item.memory_type] || {
            label: item.memory_type,
            color: "default",
          };
          return (
            <List.Item
              style={{ padding: "6px 12px" }}
              actions={[
                <Popconfirm
                  key="delete"
                  title="确认删除此记忆？"
                  onConfirm={() => handleDelete(item.id)}
                >
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                  />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <span style={{ fontSize: 12 }}>
                    <Tag color={typeInfo.color} style={{ fontSize: 10 }}>
                      {typeInfo.label}
                    </Tag>
                    <Text ellipsis style={{ maxWidth: 140 }}>
                      {item.content}
                    </Text>
                  </span>
                }
                description={
                  <Text style={{ fontSize: 10, color: "#bbb" }}>
                    重要性: {item.importance.toFixed(1)}
                  </Text>
                }
              />
            </List.Item>
          );
        }}
      />
    </div>
  );
}
