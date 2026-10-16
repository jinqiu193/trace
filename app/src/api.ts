// 与 Rust 边车 + FastAPI 后端的通信层
import { invoke } from "@tauri-apps/api/core";

export interface StatusResponse {
  stats: {
    roots: number;
    files: number;
    files_indexed: number;
    files_metadata_only: number;
    slices: number;
    db_size_mb: number;
  };
  roots: string[];
  embedder: {
    provider?: string;
    vision_provider?: string | null;
    inputs?: string[];
    outputs?: string[];
    model?: string;
    vision?: boolean;
    error?: string;
  };
  fingerprint: string;
  db_path: string;
  task?: TaskBrief;
  watch?: { enabled: boolean; interval_sec: number };
}

export interface TaskBrief {
  running: boolean;
  kind?: string;
  root?: string;
  by?: string;
  scanned?: number;
  total?: number;
  indexed?: number;
  errors?: number;
}

export interface SearchResult {
  score: number;
  path: string;
  name: string;
  file_type: string;
  location: string;
  cos_sim: number;
  z: number;
  coverage: number;
  keyword_rank: number | null;
  snippet: string;
  mtime: number;
  strong: boolean;
  weak_fallback?: boolean;
  fuzzy_match?: boolean;
  nearby_frames?: { t: number; loc: string }[];
  confidence?: "high" | "medium" | "low";
}

export interface SearchResponse {
  query: string;
  ms: number;
  results: SearchResult[];
}

export interface IndexStats {
  scanned: number;
  indexed: number;
  skipped: number;
  metadata_only: number;
  errors: number;
  removed: number;
  seconds: number;
  total?: number;
  cancelled?: boolean;
  current?: string;
}

export interface IndexTaskStatus {
  running: boolean;
  kind: string;
  root: string;
  full: boolean;
  by: string;
  progress: IndexStats | null;
  result: IndexStats | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

let _port: number | null = null;
let _base: string | null = null;

async function base(): Promise<string> {
  if (_base) return _base;
  _port = await invoke<number>("backend_url");
  _base = `http://127.0.0.1:${_port}`;
  return _base;
}

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const b = await base();
  const r = await fetch(`${b}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`${method} ${path} → ${r.status}: ${txt}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  /** 同步取基础 URL（必须在第一次 await 任意方法之后才有效） */
  baseUrl(): string {
    if (!_base) throw new Error("api.baseUrl() called before backend ready");
    return _base;
  },
  status: () => json<StatusResponse>("GET", "/api/status"),
  doctor: () => json<StatusResponse & { checks: { name: string; exists: boolean; size_mb: number; path: string }[] }>("GET", "/api/doctor"),
  search: (query: string, opts?: { limit?: number; offset?: number; file_type?: string; dir?: string; threshold?: number }) =>
    json<SearchResponse>("POST", "/api/search", {
      query, limit: opts?.limit ?? 10, offset: opts?.offset ?? 0,
      file_type: opts?.file_type, dir: opts?.dir, threshold: opts?.threshold,
    }),
  /** 提交后台索引任务（立即返回，进度走 indexStatus 轮询） */
  index: (path: string, full = false) =>
    json<{ ok: boolean; path: string; task: TaskBrief }>("POST", "/api/index", { path, full }),
  indexStatus: () => json<IndexTaskStatus>("GET", "/api/index/status"),
  indexCancel: () => json<{ ok: boolean }>("POST", "/api/index/cancel", {}),
  reindexAll: () => json<{ ok: boolean; queued: number; first: string }>("POST", "/api/reindex", {}),
  roots: () => json<{ roots: string[] }>("GET", "/api/roots"),
  addRoot: (path: string) => json<{ ok: boolean; path: string }>("POST", "/api/roots", { path }),
  removeRoot: (path: string) => json<{ ok: boolean; path: string }>("DELETE", `/api/roots/${encodeURI(path)}`),
  openPath: (path: string, location?: string) => invoke<void>("open_path", { path, location }),
  revealInFolder: (path: string) => invoke<void>("reveal_in_folder", { path }),
  /** 内嵌图片预览 URL（需等 baseUrl() 就绪） */
  imageSrc: (path: string, max_w: number = 0) => {
    const qs = max_w > 0 ? `&max_w=${max_w}` : "";
    return `${_base}/api/raw?path=${encodeURIComponent(path)}${qs}`;
  },
};
