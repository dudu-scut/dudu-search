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
