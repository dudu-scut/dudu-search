import {
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import {
  App as AntApp,
  Button,
  Drawer,
  Input,
  Popconfirm,
  Radio,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import {
  createPromptTemplate,
  deletePromptTemplate,
  getDefaultPrompt,
  listPromptTemplates,
  updatePromptTemplate,
} from "../lib/api";
import type {
  DefaultPromptResponse,
  PromptTemplate,
  PromptTemplateCreateRequest,
} from "../types";

const { TextArea } = Input;
const { Text } = Typography;

interface PromptTemplateManagerProps {
  open: boolean;
  onClose: () => void;
}

export default function PromptTemplateManager({
  open,
  onClose,
}: PromptTemplateManagerProps) {
  const { message } = AntApp.useApp();
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [defaultPrompt, setDefaultPrompt] = useState<DefaultPromptResponse | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  // 编辑表单状态
  const [editingTemplate, setEditingTemplate] = useState<PromptTemplate | null>(null);
  const [showEditor, setShowEditor] = useState(false);
  const [formName, setFormName] = useState("");
  const [formScope, setFormScope] = useState<"group" | "user">("user");
  const [formPrompt, setFormPrompt] = useState("");
  const [formActive, setFormActive] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listPromptTemplates();
      setTemplates(res.templates || []);
    } catch {
      message.error("加载提示词模板失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  const fetchDefaultPrompt = useCallback(async () => {
    try {
      const res = await getDefaultPrompt();
      setDefaultPrompt(res);
    } catch {
      // 静默失败
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchTemplates();
      fetchDefaultPrompt();
    }
  }, [open, fetchTemplates, fetchDefaultPrompt]);

  function resetForm() {
    setEditingTemplate(null);
    setFormName("");
    setFormScope("user");
    setFormPrompt("");
    setFormActive(true);
  }

  function handleCreate() {
    resetForm();
    setShowEditor(true);
  }

  function handleEdit(record: PromptTemplate) {
    setEditingTemplate(record);
    setFormName(record.name);
    setFormScope(record.scope);
    setFormPrompt(record.system_prompt);
    setFormActive(record.is_active);
    setShowEditor(true);
  }

  async function handleSave() {
    if (!formName.trim()) {
      message.warning("请输入模板名称");
      return;
    }
    if (!formPrompt.trim()) {
      message.warning("请输入提示词内容");
      return;
    }
    setSaving(true);
    try {
      if (editingTemplate) {
        await updatePromptTemplate(editingTemplate.id, {
          name: formName,
          system_prompt: formPrompt,
          is_active: formActive,
        });
        message.success("模板已更新");
      } else {
        const data: PromptTemplateCreateRequest = {
          name: formName,
          scope: formScope,
          system_prompt: formPrompt,
          is_active: formActive,
        };
        await createPromptTemplate(data);
        message.success("模板已创建");
      }
      setShowEditor(false);
      resetForm();
      fetchTemplates();
      fetchDefaultPrompt();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(templateId: string) {
    try {
      await deletePromptTemplate(templateId);
      message.success("模板已删除");
      fetchTemplates();
      fetchDefaultPrompt();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "删除失败");
    }
  }

  async function handleToggleActive(record: PromptTemplate) {
    try {
      await updatePromptTemplate(record.id, {
        is_active: !record.is_active,
      });
      message.success(record.is_active ? "已停用" : "已启用");
      fetchTemplates();
      fetchDefaultPrompt();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "操作失败");
    }
  }

  const columns: ColumnsType<PromptTemplate> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      width: 140,
    },
    {
      title: "范围",
      dataIndex: "scope",
      key: "scope",
      width: 80,
      render: (scope: string) => (
        <Tag color={scope === "group" ? "blue" : "green"}>
          {scope === "group" ? "组级" : "个人"}
        </Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      key: "is_active",
      width: 70,
      render: (active: boolean, record: PromptTemplate) => (
        <Switch
          size="small"
          checked={active}
          onChange={() => handleToggleActive(record)}
        />
      ),
    },
    {
      title: "提示词",
      dataIndex: "system_prompt",
      key: "system_prompt",
      ellipsis: true,
      render: (text: string) => (
        <Tooltip title={text.length > 60 ? text.slice(0, 100) + "..." : text}>
          <Text style={{ maxWidth: 240 }} ellipsis>
            {text}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 100,
      render: (_, record: PromptTemplate) => (
        <Space size="small">
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          />
          <Popconfirm
            title="确定删除此模板？"
            onConfirm={() => handleDelete(record.id)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button type="text" size="small" icon={<DeleteOutlined />} danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Drawer
        title="提示词模板管理"
        open={open}
        onClose={onClose}
        width={720}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            新建模板
          </Button>
        }
      >
        {/* 当前生效提示词 */}
        <div style={{ marginBottom: 16 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <Text strong>当前生效提示词</Text>
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => setShowPreview(true)}
            >
              预览
            </Button>
          </div>
          {defaultPrompt && (
            <Tag color={defaultPrompt.source === "custom" ? "orange" : "default"}>
              {defaultPrompt.source === "custom"
                ? "自定义模板"
                : "系统默认（YAML）"}
            </Tag>
          )}
        </div>

        <Table
          columns={columns}
          dataSource={templates}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
          locale={{ emptyText: "暂无自定义模板，使用系统默认提示词" }}
        />
      </Drawer>

      {/* 编辑/创建抽屉 */}
      <Drawer
        title={editingTemplate ? "编辑模板" : "新建模板"}
        open={showEditor}
        onClose={() => {
          setShowEditor(false);
          resetForm();
        }}
        width={640}
        extra={
          <Space>
            <Button onClick={() => { setShowEditor(false); resetForm(); }}>
              取消
            </Button>
            <Button type="primary" loading={saving} onClick={handleSave}>
              保存
            </Button>
          </Space>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <Text strong>模板名称</Text>
            <Input
              placeholder="例如：研究报告模板、代码分析模板"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>

          {!editingTemplate && (
            <div>
              <Text strong>作用范围</Text>
              <div style={{ marginTop: 4 }}>
                <Radio.Group
                  value={formScope}
                  onChange={(e) => setFormScope(e.target.value)}
                >
                  <Radio value="user">个人（仅自己可见）</Radio>
                  <Radio value="group">组级（组内共享）</Radio>
                </Radio.Group>
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                提示词优先级：个人模板 &gt; 组级模板 &gt; 系统默认
              </Text>
            </div>
          )}

          <div>
            <Text strong>系统提示词</Text>
            <TextArea
              rows={18}
              placeholder="输入系统提示词。支持 {current_date} 占位符，运行时自动替换为当前日期。"
              value={formPrompt}
              onChange={(e) => setFormPrompt(e.target.value)}
              style={{ marginTop: 4, fontFamily: "monospace", fontSize: 13 }}
            />
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Text strong>启用状态</Text>
            <Switch checked={formActive} onChange={setFormActive} />
            <Text type="secondary">停用后系统将使用下一级提示词</Text>
          </div>
        </div>
      </Drawer>

      {/* 预览抽屉 */}
      <Drawer
        title={
          defaultPrompt?.source === "custom"
            ? "当前生效：自定义提示词"
            : "当前生效：系统默认提示词"
        }
        open={showPreview}
        onClose={() => setShowPreview(false)}
        width={600}
      >
        <pre
          style={{
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontFamily: "monospace",
            fontSize: 13,
            lineHeight: 1.6,
            background: "rgba(0,0,0,0.02)",
            padding: 16,
            borderRadius: 8,
          }}
        >
          {defaultPrompt?.system_prompt || "加载中..."}
        </pre>
      </Drawer>
    </>
  );
}
