import { API_BASE_URL } from "./config";
import { clearToken, getToken } from "./auth";
import type {
  CancelTaskResponse,
  DefaultPromptResponse,
  FileListResponse,
  KnowledgeBaseCreateRequest,
  KnowledgeBaseIngestResponse,
  KnowledgeBaseListResponse,
  PromptTemplateCreateRequest,
  PromptTemplateListResponse,
  PromptTemplateUpdateRequest,
  SessionDetail,
  SessionListResponse,
  ShareLink,
  ShareListResponse,
  SharedSessionResponse,
  TaskResponse,
  UploadResponse,
} from "../types";

function apiUrl(path: string): string {
  return API_BASE_URL ? `${API_BASE_URL}${path}` : `${window.location.origin}${path}`;
}

function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...authHeader(),
    ...(init?.headers as Record<string, string> | undefined),
  };
  const response = await fetch(input, { ...init, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      window.dispatchEvent(new Event("auth:expired"));
    }
    const message =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`;
    throw new Error(message);
  }

  return payload as T;
}

export async function startTask(query: string, threadId: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>(apiUrl("/api/task"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query,
      thread_id: threadId
    })
  });
}

export async function cancelTask(threadId: string): Promise<CancelTaskResponse> {
  return requestJson<CancelTaskResponse>(apiUrl(`/api/task/${encodeURIComponent(threadId)}/cancel`), {
    method: "POST"
  });
}

export async function uploadSessionFiles(
  files: File[],
  threadId: string
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("thread_id", threadId);
  files.forEach((file) => formData.append("files", file));

  return requestJson<UploadResponse>(apiUrl("/api/upload"), {
    method: "POST",
    body: formData
  });
}

export async function deleteUploadedFile(
  threadId: string,
  filename: string
): Promise<{ status: string; filename: string }> {
  return requestJson(
    apiUrl(`/api/upload/${encodeURIComponent(threadId)}/${encodeURIComponent(filename)}`),
    { method: "DELETE" }
  );
}

export async function listSessionFiles(path: string): Promise<FileListResponse> {
  const url = new URL(apiUrl("/api/files"));
  url.searchParams.set("path", path);
  return requestJson<FileListResponse>(url);
}

export function getDownloadUrl(path: string): string {
  const url = new URL(apiUrl("/api/download"));
  url.searchParams.set("path", path);
  return url.toString();
}

export async function listSessions(
  limit = 20,
  offset = 0
): Promise<SessionListResponse> {
  const url = new URL(apiUrl("/api/sessions"));
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("offset", String(offset));
  return requestJson<SessionListResponse>(url);
}

export async function getSessionDetail(
  threadId: string
): Promise<SessionDetail> {
  return requestJson<SessionDetail>(
    apiUrl(`/api/sessions/${encodeURIComponent(threadId)}`)
  );
}

export async function deleteSession(
  threadId: string
): Promise<{ status: string; thread_id: string }> {
  return requestJson(
    apiUrl(`/api/sessions/${encodeURIComponent(threadId)}`),
    { method: "DELETE" }
  );
}

// ── 会话分享 API ──

/** 公开接口：通过分享 token 获取只读会话（无需认证） */
export async function getSharedSession(token: string): Promise<SharedSessionResponse> {
  return requestJson<SharedSessionResponse>(
    apiUrl(`/api/shared/${encodeURIComponent(token)}`)
  );
}

/** 创建会话分享链接 */
export async function createShareLink(
  threadId: string,
  options?: { title?: string; expiresHours?: number }
): Promise<ShareLink> {
  return requestJson<ShareLink>(
    apiUrl(`/api/sessions/${encodeURIComponent(threadId)}/share`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: options?.title,
        expires_hours: options?.expiresHours,
      }),
    }
  );
}

/** 获取会话的所有分享链接 */
export async function listShareLinks(threadId: string): Promise<ShareListResponse> {
  return requestJson<ShareListResponse>(
    apiUrl(`/api/sessions/${encodeURIComponent(threadId)}/shares`)
  );
}

/** 撤销分享链接 */
export async function revokeShareLink(shareToken: string): Promise<{ status: string }> {
  return requestJson(
    apiUrl(`/api/shared/${encodeURIComponent(shareToken)}`),
    { method: "DELETE" }
  );
}

// ── 提示词模板 API ──

/** 获取当前用户可见的提示词模板列表 */
export async function listPromptTemplates(): Promise<PromptTemplateListResponse> {
  return requestJson<PromptTemplateListResponse>(
    apiUrl("/api/prompt-templates")
  );
}

/** 创建新的提示词模板 */
export async function createPromptTemplate(
  data: PromptTemplateCreateRequest
): Promise<{ id: string; name: string; scope: string }> {
  return requestJson(
    apiUrl("/api/prompt-templates"),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }
  );
}

/** 更新提示词模板 */
export async function updatePromptTemplate(
  templateId: string,
  data: PromptTemplateUpdateRequest
): Promise<{ status: string }> {
  return requestJson(
    apiUrl(`/api/prompt-templates/${encodeURIComponent(templateId)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }
  );
}

/** 删除提示词模板 */
export async function deletePromptTemplate(
  templateId: string
): Promise<{ status: string }> {
  return requestJson(
    apiUrl(`/api/prompt-templates/${encodeURIComponent(templateId)}`),
    { method: "DELETE" }
  );
}

/** 获取当前生效的系统提示词（用于预览） */
export async function getDefaultPrompt(): Promise<DefaultPromptResponse> {
  return requestJson<DefaultPromptResponse>(
    apiUrl("/api/prompt-templates/default")
  );
}

// ── 知识库管理 API ──

/** 获取当前用户组可见的知识库列表 */
export async function listKnowledgeBases(): Promise<KnowledgeBaseListResponse> {
  return requestJson<KnowledgeBaseListResponse>(
    apiUrl("/api/kb/list")
  );
}

/** 创建新的知识库 */
export async function createKnowledgeBase(
  data: KnowledgeBaseCreateRequest
): Promise<{ status: string; kb_id: string; name: string }> {
  return requestJson(
    apiUrl("/api/kb/create"),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }
  );
}

/** 删除知识库 */
export async function deleteKnowledgeBase(
  kbName: string
): Promise<{ status: string; name: string }> {
  return requestJson(
    apiUrl(`/api/kb/${encodeURIComponent(kbName)}`),
    { method: "DELETE" }
  );
}

/** 向知识库摄入文档文件 */
export async function ingestKBFiles(
  kbName: string,
  files: File[]
): Promise<KnowledgeBaseIngestResponse> {
  const formData = new FormData();
  formData.append("kb_name", kbName);
  for (const file of files) {
    formData.append("files", file);
  }
  const headers: Record<string, string> = {
    ...authHeader(),
  };
  const response = await fetch(apiUrl("/api/kb/ingest"), {
    method: "POST",
    headers,
    body: formData,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `摄入失败 (${response.status})`);
  }
  return response.json() as Promise<KnowledgeBaseIngestResponse>;
}

// ── RBAC 管理 API ──

import type {
  AdminUserListResponse,
  RoleListResponse,
} from "../types";

/** 获取用户列表（含角色信息，仅管理员） */
export async function listAdminUsers(
  limit = 100,
  offset = 0
): Promise<AdminUserListResponse> {
  return requestJson<AdminUserListResponse>(
    apiUrl(`/api/admin/users?limit=${limit}&offset=${offset}`)
  );
}

/** 修改用户角色 */
export async function updateUserRole(
  userId: string,
  role: string
): Promise<{ status: string }> {
  return requestJson(
    apiUrl(`/api/admin/users/${encodeURIComponent(userId)}/role`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    }
  );
}

/** 获取角色列表（含权限集合） */
export async function listRoles(): Promise<RoleListResponse> {
  return requestJson<RoleListResponse>(apiUrl("/api/admin/roles"));
}

/** 创建自定义角色 */
export async function createRole(data: {
  name: string;
  display_name: string;
  description?: string;
  permission_ids?: string[];
}): Promise<{ status: string }> {
  return requestJson(apiUrl("/api/admin/roles"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/** 修改角色权限集合（全量替换） */
export async function updateRolePermissions(
  roleName: string,
  permissionIds: string[]
): Promise<{ status: string }> {
  return requestJson(
    apiUrl(`/api/admin/roles/${encodeURIComponent(roleName)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permission_ids: permissionIds }),
    }
  );
}
