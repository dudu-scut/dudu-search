/**
 * RBAC 用户与角色管理 Drawer 组件
 *
 * 仅管理员可见。包含两个 Tab：
 * 1. 用户管理 — 用户列表 + 行内角色切换
 * 2. 角色管理 — 角色列表 + 权限编辑 + 创建自定义角色
 */

import { useCallback, useEffect, useState } from "react";
import {
  Drawer,
  Table,
  Select,
  Tag,
  Button,
  Space,
  Modal,
  Input,
  Checkbox,
  Empty,
  Spin,
  Tabs,
  Typography,
  message,
  Tooltip,
} from "antd";
import {
  UserOutlined,
  SafetyOutlined,
  ReloadOutlined,
  PlusOutlined,
  EditOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import {
  listAdminUsers,
  updateUserRole,
  listRoles,
  createRole,
  updateRolePermissions,
} from "../lib/api";
import type {
  AdminUser,
  Role,
  Permission,
} from "../types";

const { TextArea } = Input;
const { Text } = Typography;

// ── 角色颜色映射 ──

const ROLE_COLORS: Record<string, string> = {
  admin: "red",
  manager: "orange",
  user: "blue",
  viewer: "default",
};

// ── 主组件 ──

interface UserManagerProps {
  open: boolean;
  onClose: () => void;
}

export default function UserManager({ open, onClose }: UserManagerProps) {
  const [activeTab, setActiveTab] = useState("users");

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={800}
      title={
        <Space>
          <UserOutlined />
          <span>用户与权限管理</span>
        </Space>
      }
      destroyOnClose
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "users",
            label: (
              <span>
                <UserOutlined /> 用户管理
              </span>
            ),
            children: <UsersTab />,
          },
          {
            key: "roles",
            label: (
              <span>
                <SafetyOutlined /> 角色管理
              </span>
            ),
            children: <RolesTab />,
          },
        ]}
      />
    </Drawer>
  );
}

// ── 用户管理 Tab ──

function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const fetchUsers = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const res = await listAdminUsers(pageSize, (p - 1) * pageSize);
      setUsers(res.users);
      setTotal(res.total);
    } catch (e: any) {
      message.error(`加载用户列表失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers(page);
  }, [page, fetchUsers]);

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      await updateUserRole(userId, newRole);
      message.success(`角色已更新为 ${newRole}`);
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, role: newRole } : u))
      );
    } catch (e: any) {
      message.error(`更新角色失败: ${e.message}`);
    }
  };

  const columns: ColumnsType<AdminUser> = [
    {
      title: "用户名",
      dataIndex: "username",
      key: "username",
      width: 140,
    },
    {
      title: "角色",
      dataIndex: "role",
      key: "role",
      width: 160,
      render: (role: string, record: AdminUser) => (
        <Select
          value={role}
          size="small"
          style={{ width: 130 }}
          onChange={(val) => handleRoleChange(record.id, val)}
          options={[
            { value: "admin", label: "系统管理员" },
            { value: "manager", label: "组管理员" },
            { value: "user", label: "普通用户" },
            { value: "viewer", label: "只读用户" },
          ]}
        />
      ),
    },
    {
      title: "用户组",
      dataIndex: "group_name",
      key: "group_name",
      width: 120,
      render: (name: string | null) => name || <Text type="secondary">—</Text>,
    },
    {
      title: "来源",
      dataIndex: "auth_source",
      key: "auth_source",
      width: 80,
      render: (src: string) => (
        <Tag color={src === "local" ? "default" : "cyan"}>{src}</Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      key: "is_active",
      width: 70,
      render: (active: boolean) => (
        <Tag color={active ? "green" : "default"}>
          {active ? "启用" : "禁用"}
        </Tag>
      ),
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 12, textAlign: "right" }}>
        <Button
          icon={<ReloadOutlined />}
          size="small"
          onClick={() => fetchUsers(page)}
          loading={loading}
        >
          刷新
        </Button>
      </div>
      <Table
        dataSource={users}
        columns={columns}
        rowKey="id"
        size="small"
        loading={loading}
        pagination={{
          current: page,
          pageSize,
          total,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 个用户`,
        }}
      />
    </>
  );
}

// ── 角色管理 Tab ──

function RolesTab() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [allPermissions, setAllPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(false);
  const [editRole, setEditRole] = useState<Role | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  const fetchRoles = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listRoles();
      setRoles(res.roles);
      setAllPermissions(res.all_permissions);
    } catch (e: any) {
      message.error(`加载角色列表失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRoles();
  }, [fetchRoles]);

  // 按资源分组权限
  const groupedPerms: Record<string, Permission[]> = {};
  for (const p of allPermissions) {
    if (!groupedPerms[p.resource]) groupedPerms[p.resource] = [];
    groupedPerms[p.resource].push(p);
  }

  return (
    <>
      <div style={{ marginBottom: 12, display: "flex", justifyContent: "space-between" }}>
        <Text type="secondary">管理系统角色及其权限分配</Text>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={fetchRoles}
            loading={loading}
          >
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            size="small"
            onClick={() => setShowCreateModal(true)}
          >
            创建角色
          </Button>
        </Space>
      </div>

      <Spin spinning={loading}>
        {roles.length === 0 ? (
          <Empty description="暂无角色数据" />
        ) : (
          roles.map((role) => (
            <div
              key={role.name}
              style={{
                marginBottom: 16,
                padding: 16,
                border: "1px solid var(--ant-color-border, #e8e8e8)",
                borderRadius: 8,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <Space>
                  <Tag color={ROLE_COLORS[role.name] || "blue"}>{role.display_name}</Tag>
                  {role.is_system && <Tag>内置</Tag>}
                  <Text type="secondary">{role.description}</Text>
                </Space>
                <Button
                  icon={<EditOutlined />}
                  size="small"
                  onClick={() => setEditRole(role)}
                >
                  编辑权限
                </Button>
              </div>
              <div>
                {role.permissions.length === 0 ? (
                  <Text type="secondary">无权限</Text>
                ) : (
                  role.permissions.map((pid) => (
                    <Tag key={pid} style={{ marginBottom: 4 }}>
                      {pid}
                    </Tag>
                  ))
                )}
              </div>
            </div>
          ))
        )}
      </Spin>

      {/* 编辑权限 Modal */}
      {editRole && (
        <EditPermissionsModal
          role={editRole}
          groupedPerms={groupedPerms}
          onClose={() => setEditRole(null)}
          onSaved={() => {
            setEditRole(null);
            fetchRoles();
          }}
        />
      )}

      {/* 创建角色 Modal */}
      {showCreateModal && (
        <CreateRoleModal
          groupedPerms={groupedPerms}
          onClose={() => setShowCreateModal(false)}
          onCreated={() => {
            setShowCreateModal(false);
            fetchRoles();
          }}
        />
      )}
    </>
  );
}

// ── 编辑权限 Modal ──

interface EditPermsProps {
  role: Role;
  groupedPerms: Record<string, Permission[]>;
  onClose: () => void;
  onSaved: () => void;
}

function EditPermissionsModal({ role, groupedPerms, onClose, onSaved }: EditPermsProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set(role.permissions));
  const [saving, setSaving] = useState(false);

  const togglePerm = (pid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  };

  const toggleResource = (perms: Permission[], checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const p of perms) {
        if (checked) next.add(p.id);
        else next.delete(p.id);
      }
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateRolePermissions(role.name, Array.from(selected));
      message.success("权限已更新");
      onSaved();
    } catch (e: any) {
      message.error(`更新权限失败: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const RESOURCE_LABELS: Record<string, string> = {
    task: "任务",
    file: "文件",
    session: "会话",
    memory: "记忆",
    kb: "知识库",
    prompt: "提示词",
    metric: "监控",
    user: "用户",
    role: "角色",
    worker: "Worker",
    share: "分享",
  };

  return (
    <Modal
      open
      title={`编辑权限 — ${role.display_name}`}
      onCancel={onClose}
      onOk={handleSave}
      confirmLoading={saving}
      width={520}
    >
      {Object.entries(groupedPerms).map(([resource, perms]) => {
        const allChecked = perms.every((p) => selected.has(p.id));
        const someChecked = perms.some((p) => selected.has(p.id));
        return (
          <div key={resource} style={{ marginBottom: 12 }}>
            <div style={{ marginBottom: 4 }}>
              <Checkbox
                checked={allChecked}
                indeterminate={!allChecked && someChecked}
                onChange={(e) => toggleResource(perms, e.target.checked)}
              >
                <Text strong>{RESOURCE_LABELS[resource] || resource}</Text>
              </Checkbox>
            </div>
            <div style={{ paddingLeft: 24 }}>
              {perms.map((p) => (
                <Checkbox
                  key={p.id}
                  checked={selected.has(p.id)}
                  onChange={() => togglePerm(p.id)}
                  style={{ marginRight: 12 }}
                >
                  <Tooltip title={p.description}>
                    {p.action}
                  </Tooltip>
                </Checkbox>
              ))}
            </div>
          </div>
        );
      })}
    </Modal>
  );
}

// ── 创建角色 Modal ──

interface CreateRoleProps {
  groupedPerms: Record<string, Permission[]>;
  onClose: () => void;
  onCreated: () => void;
}

function CreateRoleModal({ groupedPerms, onClose, onCreated }: CreateRoleProps) {
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  const togglePerm = (pid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  };

  const handleCreate = async () => {
    if (!name.trim() || !displayName.trim()) {
      message.warning("请填写角色标识和显示名称");
      return;
    }
    setSaving(true);
    try {
      await createRole({
        name: name.trim(),
        display_name: displayName.trim(),
        description: description.trim(),
        permission_ids: Array.from(selected),
      });
      message.success("角色创建成功");
      onCreated();
    } catch (e: any) {
      message.error(`创建角色失败: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title="创建自定义角色"
      onCancel={onClose}
      onOk={handleCreate}
      confirmLoading={saving}
      width={520}
    >
      <div style={{ marginBottom: 12 }}>
        <Text>角色标识（英文，如 editor）</Text>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="editor"
          style={{ marginTop: 4 }}
        />
      </div>
      <div style={{ marginBottom: 12 }}>
        <Text>显示名称</Text>
        <Input
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="编辑员"
          style={{ marginTop: 4 }}
        />
      </div>
      <div style={{ marginBottom: 16 }}>
        <Text>描述</Text>
        <TextArea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="该角色的用途说明"
          rows={2}
          style={{ marginTop: 4 }}
        />
      </div>
      <Text strong>分配权限</Text>
      <div style={{ marginTop: 8, maxHeight: 240, overflow: "auto" }}>
        {Object.entries(groupedPerms).map(([resource, perms]) => (
          <div key={resource} style={{ marginBottom: 8, paddingLeft: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>{resource}: </Text>
            {perms.map((p) => (
              <Checkbox
                key={p.id}
                checked={selected.has(p.id)}
                onChange={() => togglePerm(p.id)}
                style={{ marginRight: 8 }}
              >
                {p.action}
              </Checkbox>
            ))}
          </div>
        ))}
      </div>
    </Modal>
  );
}
