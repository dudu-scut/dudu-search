/** 前端认证工具 — token 存储 + fetch 封装 */

const TOKEN_KEY = "deepagents_token";
const USER_KEY = "deepagents_user";

export interface User {
  id: string;
  username: string;
  role: string;
  group_id: number;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function getUser(): User | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setUser(user: User): void {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function isLoggedIn(): boolean {
  return !!getToken();
}

export function logout(): void {
  clearToken();
  window.location.href = "/";
}

/** Handle OIDC SSO callback — extract token from URL hash fragment and store it. */
export function handleSSOCallback(): boolean {
  const hash = window.location.hash;
  const params = new URLSearchParams(hash.replace(/^#/, "?"));
  const token = params.get("token");
  if (token) {
    // Parse JWT payload to get user info
    try {
      const payload = JSON.parse(atob(token.split(".")[1]));
      setToken(token);
      setUser({
        id: payload.sub,
        username: payload.username,
        role: payload.role,
        group_id: payload.group_id,
      });
    } catch {
      // If parsing fails, still store the token
      setToken(token);
    }
    // Clean URL (remove hash fragment)
    window.location.hash = "";
    return true;
  }
  return false;
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

export async function authFetch(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = {
    "Content-Type": "application/json",
    ...authHeaders(),
    ...(options.headers || {}),
  };
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    clearToken();
    window.location.href = "/";
    throw new Error("登录已过期，请重新登录");
  }
  return response;
}
