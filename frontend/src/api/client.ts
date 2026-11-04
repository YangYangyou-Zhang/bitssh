const HASH_PARAMS = new URLSearchParams(
  location.hash.startsWith("#") ? location.hash.slice(1) : "",
);
export const TOKEN = HASH_PARAMS.get("token") ?? "";

export class ApiError extends Error {
  constructor(public code: string, public status: number, raw?: string) {
    super(raw || code);
  }
}

export interface AuthSpec {
  method: "password" | "key" | "agent" | "kbdint";
  credential_id?: string | null;
  key_path?: string | null;
  key_credential_id?: string | null;
}

export interface Profile {
  id: string;
  name: string;
  protocol: "ssh" | "serial";
  folder_id?: string | null;
  host: string;
  port: number;
  username: string;
  auth: AuthSpec;
  jump: unknown[];
  forwards: unknown[];
  serial: Record<string, unknown>;
  terminal: { term: string; scrollback: number };
  tags: string[];
  note: string;
}

export interface FsEntry {
  name: string;
  dir: boolean;
  link?: boolean;
  size: number;
  mtime: number;
}

export interface KnownHost {
  host: string;
  port: number;
  key_type: string;
  fingerprint: string | null;
  added_at: string;
}

export interface KnownHostsResponse {
  path: string;
  known_hosts: KnownHost[];
}

export interface Forward {
  id: string;
  session_id: string;
  type: "local" | "remote" | "socks";
  bind_host: string;
  bind_port: number;
  dest_host: string;
  dest_port: number;
}

export interface EditSync {
  id: string;
  session_id: string;
  remote_path: string;
  local_path: string;
  status: "downloading" | "ready" | "uploading" | "synced" | "conflict" | "error";
  error: string | null;
  updated_at: string;
}

export interface Transfer {
  id: string;
  kind: "download" | "upload";
  label: string;
  target?: string | null;
  status: "running" | "done" | "failed" | "cancelled";
  copied: number;
  total: number;
  error: string | null;
}

export type Theme = Record<string, string>;

export interface ThemeResponse {
  path: string;
  theme: Theme;
  defaults?: Theme;
}

export interface WorkspaceState {
  path: string;
  dirty: boolean;
  conflict: boolean;
  recent: string[];
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Bitssh-Token", TOKEN);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const resp = await fetch(path, { ...init, headers });
  const text = await resp.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!resp.ok) {
    const raw =
      (body as { detail?: string; error?: string } | null)?.detail ??
      (body as { error?: string } | null)?.error ??
      "UNKNOWN";
    throw new ApiError(String(raw).split(":")[0], resp.status, String(raw));
  }
  return body as T;
}

export const api = {
  ping: () => request<{ ok: boolean; backend: string }>("/api/ping"),
  workspace: () => request<WorkspaceState>("/api/workspace"),
  getTheme: () => request<ThemeResponse>("/api/theme"),
  saveTheme: (theme: Theme) =>
    request<ThemeResponse>("/api/theme", {
      method: "PUT",
      body: JSON.stringify({ theme }),
    }),
  resetTheme: () => request<ThemeResponse>("/api/theme", { method: "DELETE" }),
  knownHosts: () => request<KnownHostsResponse>("/api/known-hosts"),
  deleteKnownHost: (host: string, port: number, keyType: string) => {
    const query = new URLSearchParams({
      host,
      port: String(port),
      key_type: keyType,
    });
    return request<KnownHostsResponse>(`/api/known-hosts?${query.toString()}`, {
      method: "DELETE",
    });
  },
  saveWorkspace: (force = false) =>
    request<WorkspaceState>("/api/workspace/save", {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  list: () =>
    request<{ workspace: { profiles: Profile[]; folders: { id: string; name: string }[] }; path: string; dirty: boolean; conflict: boolean }>(
      "/api/profiles",
    ),
  createProfile: (profile: Profile) =>
    request<Profile>("/api/profiles", { method: "POST", body: JSON.stringify(profile) }),
  updateProfile: (profile: Profile) =>
    request<Profile>(`/api/profiles/${profile.id}`, {
      method: "PUT",
      body: JSON.stringify(profile),
    }),
  deleteProfile: (id: string) =>
    request<{ ok: boolean }>(`/api/profiles/${id}`, { method: "DELETE" }),
  createSession: (profileId: string, password?: string) =>
    request<{ session_id: string; state: string; error?: string | null }>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, password }),
    }),
  createSerialSession: (profileId: string) =>
    request<{ session_id: string; state: string }>("/api/serial/sessions", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId }),
    }),
  serialPorts: () =>
    request<{ device: string; description: string; hwid: string; vid?: number; pid?: number; serial?: string }[]>(
      "/api/serial/ports",
    ),
  deleteSession: (id: string) =>
    request<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),
  hostkey: (id: string, decision: "once" | "always" | "reject") =>
    request<{ ok: boolean }>(`/api/sessions/${id}/hostkey`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  kbdint: (id: string, responses: string[]) =>
    request<{ ok: boolean }>(`/api/sessions/${id}/kbdint`, {
      method: "POST",
      body: JSON.stringify({ responses }),
    }),
  exportProfiles: () => request<Record<string, unknown>>("/api/profiles/export"),
  importProfiles: (payload: unknown, mode = "copy") =>
    request<{ ok: boolean; imported: number }>(`/api/profiles/import?mode=${mode}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  hasSecret: (id: string) => request<{ exists: boolean }>(`/api/credentials/${id}`),
  sftpHome: (sid: string) => request<{ path: string }>(`/api/sftp/${sid}/home`),
  sftpList: (sid: string, path: string) =>
    request<{ path: string; entries: FsEntry[] }>(
      `/api/sftp/${sid}/list?path=${encodeURIComponent(path)}`,
    ),
  sftpOps: (sid: string, body: Record<string, unknown>) =>
    request<{ ok: boolean }>(`/api/sftp/${sid}/ops`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  sftpDownload: (sid: string, path: string, localPath?: string, overwrite = false) =>
    request<{ id: string; local_path: string }>(`/api/sftp/${sid}/download`, {
      method: "POST",
      body: JSON.stringify({ path, local_path: localPath, overwrite }),
    }),
  uploadLocal: (sid: string, localPath: string, remoteDir: string) =>
    request<{ id: string }>(`/api/sftp/${sid}/upload-local`, {
      method: "POST",
      body: JSON.stringify({ local_path: localPath, remote_dir: remoteDir }),
    }),
  downloadTree: (sid: string, path: string, localDir: string, overwrite = false) =>
    request<{ id: string; local_path: string }>(`/api/sftp/${sid}/download-tree`, {
      method: "POST",
      body: JSON.stringify({ path, local_dir: localDir, overwrite }),
    }),
  sftpUpload: (sid: string, localPath: string, path: string) =>
    request<{ id: string }>(`/api/sftp/${sid}/upload`, {
      method: "POST",
      body: JSON.stringify({ local_path: localPath, path }),
    }),
  localList: (path: string) =>
    request<{ path: string; parent: string | null; entries: FsEntry[] }>(
      `/api/local/list?path=${encodeURIComponent(path)}`,
    ),
  localOps: (body: Record<string, unknown>) =>
    request<{ ok: boolean }>("/api/local/ops", { method: "POST", body: JSON.stringify(body) }),
  forwards: () => request<Forward[]>("/api/forwards"),
  createForward: (body: Record<string, unknown>) =>
    request<Forward>("/api/forwards", { method: "POST", body: JSON.stringify(body) }),
  deleteForward: (id: string) =>
    request<{ ok: boolean }>(`/api/forwards/${id}`, { method: "DELETE" }),
  uploadStream: (sid: string, path: string, file: File) =>
    fetch(`/api/sftp/${sid}/upload-stream?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      headers: {
        "X-Bitssh-Token": TOKEN,
        "X-File-Size": String(file.size),
      },
      // A Blob body is streamed from disk by Chromium and avoids the
      // ReadableStream/duplex path, which is not universally supported.
      body: file,
    }).then(async (resp) => {
      if (!resp.ok) throw new ApiError("TRANSFER_FAILED", resp.status);
      return (await resp.json()) as { id: string; bytes: number };
    }),
  openPath: (path: string) =>
    request<{ ok: boolean }>("/api/system/open-path", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  editRemote: (sid: string, path: string) =>
    request<EditSync>(`/api/sftp/${sid}/edit`, {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  edits: () => request<EditSync[]>("/api/edits"),
  uploadEdit: (id: string) =>
    request<{ ok: boolean }>(`/api/edits/${id}/upload`, { method: "POST" }),
  resolveEdit: (id: string, action: "overwrite" | "reload") =>
    request<{ ok: boolean }>(`/api/edits/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  stopEdit: (id: string) =>
    request<{ ok: boolean }>(`/api/edits/${id}`, { method: "DELETE" }),
  downloadTicket: (sid: string, path: string) =>
    request<{ ticket: string; url: string }>(`/api/sftp/${sid}/download-ticket`, {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  transfers: () => request<Transfer[]>("/api/transfers"),
  cancelTransfer: (id: string) =>
    request<{ ok: boolean }>(`/api/transfers/${id}/cancel`, { method: "POST" }),
  putSecret: (credentialId: string, secret: string) =>
    request<{ ok: boolean }>("/api/credentials", {
      method: "PUT",
      body: JSON.stringify({ credential_id: credentialId, secret }),
    }),
};

export function terminalUrl(sessionId: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/terminal/${sessionId}`;
}

export const WS_PROTOCOLS = ["bitssh.v1", TOKEN];
