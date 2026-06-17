import {
  BookOutlined,
  DeleteOutlined,
  InboxOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  App as AntApp,
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
} from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import { useCallback, useEffect, useState } from "react";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  ingestKBFiles,
  listKnowledgeBases,
} from "../lib/api";
import type { KnowledgeBase } from "../types";

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

interface KnowledgeBaseManagerProps {
  open: boolean;
  onClose: () => void;
}

export default function KnowledgeBaseManager({
  open,
  onClose,
}: KnowledgeBaseManagerProps) {
  const { message } = AntApp.useApp();
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(false);

  // 创建表单
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  // 摄入文件
  const [ingestTarget, setIngestTarget] = useState<string | null>(null);
  const [ingestFiles, setIngestFiles] = useState<UploadFile[]>([]);
  const [ingesting, setIngesting] = useState(false);

  const fetchKBs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listKnowledgeBases();
      setKbs(res.knowledge_bases || []);
    } catch {
      message.error("加载知识库列表失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (open) {
      fetchKBs();
    }
  }, [open, fetchKBs]);

  async function handleCreate() {
    if (!newName.trim()) {
      message.warning("请输入知识库名称");
      return;
    }
    setCreating(true);
    try {
      await createKnowledgeBase({ name: newName, description: newDesc });
      message.success(`知识库 "${newName}" 创建成功`);
      setShowCreateModal(false);
      setNewName("");
      setNewDesc("");
      fetchKBs();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(kbName: string) {
    try {
      await deleteKnowledgeBase(kbName);
      message.success(`知识库 "${kbName}" 已删除`);
      fetchKBs();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "删除失败");
    }
  }

  async function handleIngest() {
    if (!ingestTarget || ingestFiles.length === 0) {
      message.warning("请选择要摄入的文件");
      return;
    }
    setIngesting(true);
    try {
      const rawFiles = ingestFiles
        .map((f) => f.originFileObj)
        .filter((f): f is File => f != null);
      const res = await ingestKBFiles(ingestTarget, rawFiles);
      // 展示每个文件的摄入结果
      const entries = Object.entries(res.results);
      const successCount = entries.filter(([, v]) =>
        v.startsWith("摄入成功")
      ).length;
      const failCount = entries.length - successCount;
      if (failCount === 0) {
        message.success(`全部 ${successCount} 个文件摄入成功`);
      } else {
        message.warning(
          `${successCount} 个成功，${failCount} 个失败`,
          5
        );
      }
      setIngestTarget(null);
      setIngestFiles([]);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "摄入失败");
    } finally {
      setIngesting(false);
    }
  }

  return (
    <>
      <Drawer
        title={
          <Space>
            <BookOutlined />
            知识库管理
          </Space>
        }
        open={open}
        onClose={onClose}
        width={640}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetchKBs} size="small">
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setShowCreateModal(true)}
            >
              新建知识库
            </Button>
          </Space>
        }
      >
        <Spin spinning={loading}>
          {kbs.length === 0 ? (
            <Empty
              description="暂无知识库"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            >
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setShowCreateModal(true)}
              >
                创建第一个知识库
              </Button>
            </Empty>
          ) : (
            <List
              dataSource={kbs}
              renderItem={(kb) => (
                <Card
                  size="small"
                  style={{ marginBottom: 12 }}
                  title={
                    <Space>
                      <BookOutlined />
                      <span>{kb.name}</span>
                      <Tag color="blue">RAG</Tag>
                    </Space>
                  }
                  extra={
                    <Space size="small">
                      <Button
                        size="small"
                        icon={<InboxOutlined />}
                        onClick={() => setIngestTarget(kb.name)}
                      >
                        摄入
                      </Button>
                      <Popconfirm
                        title={`确定删除知识库 "${kb.name}"？`}
                        description="删除后不可恢复，所有已摄入的文档将丢失。"
                        onConfirm={() => handleDelete(kb.name)}
                        okText="删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                      >
                        <Button
                          size="small"
                          icon={<DeleteOutlined />}
                          danger
                        />
                      </Popconfirm>
                    </Space>
                  }
                >
                  {kb.description ? (
                    <Paragraph style={{ margin: 0 }}>{kb.description}</Paragraph>
                  ) : (
                    <Text type="secondary">暂无描述</Text>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      ID: {kb.kb_id}
                    </Text>
                  </div>
                </Card>
              )}
            />
          )}
        </Spin>
      </Drawer>

      {/* 创建知识库弹窗 */}
      <Modal
        title="新建知识库"
        open={showCreateModal}
        onOk={handleCreate}
        onCancel={() => {
          setShowCreateModal(false);
          setNewName("");
          setNewDesc("");
        }}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <Text strong>知识库名称</Text>
            <Input
              placeholder="例如：产品文档、技术手册"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Text strong>描述（可选）</Text>
            <TextArea
              rows={3}
              placeholder="简要描述知识库的用途和内容范围"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>
        </div>
      </Modal>

      {/* 摄入文件弹窗 */}
      <Modal
        title={`向 "${ingestTarget}" 摄入文档`}
        open={!!ingestTarget}
        onOk={handleIngest}
        onCancel={() => {
          setIngestTarget(null);
          setIngestFiles([]);
        }}
        confirmLoading={ingesting}
        okText="开始摄入"
        cancelText="取消"
        width={520}
      >
        <Upload.Dragger
          multiple
          accept=".pdf,.docx,.md,.txt"
          fileList={ingestFiles}
          beforeUpload={() => false}
          onChange={({ fileList }) => setIngestFiles(fileList)}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽文件到此区域</p>
          <p className="ant-upload-hint">
            支持 PDF、DOCX、Markdown、TXT 格式，可批量上传
          </p>
        </Upload.Dragger>
        {ingestFiles.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Text type="secondary">
              已选择 {ingestFiles.length} 个文件
            </Text>
          </div>
        )}
      </Modal>
    </>
  );
}
