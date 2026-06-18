export type ConnectionState = "connecting" | "connected" | "reconnecting" | "closed";

export type MonitorEventName =
  | "session_created"
  | "tool_start"
  | "assistant_call"
  | "task_result"
  | "task_cancelled"
  | "error"
  | string;

export interface MonitorMessage {
  type: "monitor_event";
  event: MonitorEventName;
  message: string;
  data: Record<string, unknown>;
  timestamp: string;
}

export interface PongMessage {
  type: "pong";
  message: string;
}

export type SocketMessage = MonitorMessage | PongMessage;

export interface TaskResponse {
  status: "started" | string;
  thread_id: string;
}

export interface CancelTaskResponse {
  status: "cancelled" | "cancelling" | string;
  thread_id: string;
  message?: string;
}

export interface UploadResponse {
  status: "uploaded" | string;
  files: string[];
}

export interface OutputFile {
  name: string;
  type: "file" | string;
  path: string;
  size: number;
  mtime: number;
}

export interface FileListResponse {
  files?: OutputFile[];
  error?: string;
}

export interface UploadedItem {
  uid: string;
  name: string;
  size: number;
  raw: File;
}

export interface SessionSummary {
  thread_id: string;
  title: string;
  status: string;
  message_count: number;
  started_at: string | null;
  completed_at: string | null;
}

export interface SessionListResponse {
  sessions: SessionSummary[];
}

export interface SessionDetail {
  thread_id: string;
  title: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  messages: SessionMessage[];
  events: SessionEvent[];
}

export interface SessionMessage {
  role: string;
  content: string | null;
  tool_calls: unknown;
  created_at: string;
}

export interface SessionEvent {
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface SharedSessionResponse {
  thread_id: string;
  title: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  messages: SessionMessage[];
  shared_by: boolean;
}

export interface ShareLink {
  share_token: string;
  title: string;
  is_active: boolean;
  view_count: number;
  expires_at: string | null;
  created_at: string | null;
  share_url: string;
}

export interface ShareListResponse {
  shares: ShareLink[];
  total: number;
}

export interface PromptTemplate {
  id: string;
  name: string;
  scope: "group" | "user";
  owner_id: string | null;
  group_id: number | null;
  agent_type: string;
  system_prompt: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface PromptTemplateListResponse {
  templates: PromptTemplate[];
  total: number;
}

export interface PromptTemplateCreateRequest {
  name: string;
  scope: "group" | "user";
  agent_type?: string;
  system_prompt: string;
  is_active?: boolean;
}

export interface PromptTemplateUpdateRequest {
  name?: string;
  system_prompt?: string;
  is_active?: boolean;
}

export interface DefaultPromptResponse {
  source: "custom" | "default";
  system_prompt: string;
}

export interface KnowledgeBase {
  name: string;
  description: string;
  kb_id: string;
}

export interface KnowledgeBaseListResponse {
  knowledge_bases: KnowledgeBase[];
}

export interface KnowledgeBaseCreateRequest {
  name: string;
  description?: string;
}

export interface KnowledgeBaseIngestResponse {
  status: string;
  kb_name: string;
  results: Record<string, string>;
}

// ── RBAC 权限系统 ──

export interface Permission {
  id: string;
  resource: string;
  action: string;
  description: string;
}

export interface Role {
  name: string;
  display_name: string;
  description: string;
  is_system: boolean;
  created_at: string | null;
  permissions: string[];
}

export interface RoleListResponse {
  roles: Role[];
  all_permissions: Permission[];
}

export interface AdminUser {
  id: string;
  username: string;
  email: string | null;
  role: string;
  is_active: boolean;
  auth_source: string;
  group_id: number | null;
  group_name: string | null;
  created_at: string | null;
}

export interface AdminUserListResponse {
  users: AdminUser[];
  total: number;
}
