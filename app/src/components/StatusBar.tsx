import { useEffect, useState } from "react";
import { api, StatusResponse } from "../api";

export function StatusBar({ tick }: { tick: number }) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // tick 变时立刻刷一次（索引完成等）；之后每 5s 轮询。
  // 索引任务运行中自动加速到 1s（后端已异步化，轮询不再是负载）。
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fetchOnce = async () => {
      let busy = false;
      try {
        const s = await api.status();
        if (!cancelled) { setStatus(s); setError(null); busy = !!s.task?.running; }
      } catch (e: any) {
        if (!cancelled) setError(String(e?.message ?? e));
      }
      if (!cancelled) {
        timer = setTimeout(fetchOnce, busy ? 1000 : 5000);
      }
    };
    fetchOnce();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [tick]);

  if (error) return <div className="statusbar error">✗ 后端连接失败：{error}</div>;
  if (!status) return <div className="statusbar">加载中…</div>;

  const s = status.stats;
  const e = status.embedder;
  const healthy = !e.error;
  const task = status.task;

  return (
    <div className={"statusbar " + (healthy ? "ok" : "warn")}>
      <span className="sb-stat">📁 {s.files_indexed} 文件</span>
      <span className="sb-stat">📑 {s.slices} 切片</span>
      <span className="sb-stat">🗄 {s.db_size_mb} MB</span>
      <span className="sb-divider">·</span>
      <span className="sb-stat" title={status.db_path}>
        模型 {e.provider}{e.vision ? ` + vision ${e.vision_provider ?? "?"}` : ""}
      </span>
      <span className="sb-stat">{status.roots.length} 个索引范围</span>
      {task?.running && (
        <span className="sb-task" title={`${task.kind} ${task.root ?? ""}（${task.by}）`}>
          ⚙ {task.by === "watcher" ? "自动同步" : "索引中"}
          {task.total ? ` ${task.scanned}/${task.total}` : ` ${task.scanned}`}
          {task.errors ? ` ⚠${task.errors}` : ""}
        </span>
      )}
      {status.watch?.enabled && !task?.running && (
        <span className="sb-stat" title={`每 ${status.watch.interval_sec}s 自动同步文件变更`}>
          👁 自动同步
        </span>
      )}
      <span className="sb-spacer" />
      <span className="sb-fp">{status.fingerprint.slice(0, 24)}…</span>
    </div>
  );
}