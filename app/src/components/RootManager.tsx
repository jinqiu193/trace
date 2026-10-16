import { useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, IndexStats, IndexTaskStatus } from "../api";

export function RootManager({ onChanged }: { onChanged?: () => void }) {
  const [roots, setRoots] = useState<string[]>([]);
  const [doneStats, setDoneStats] = useState<Record<string, IndexStats>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [task, setTask] = useState<IndexTaskStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const handledRef = useRef<string | null>(null);

  const refresh = async () => {
    try {
      setRoots((await api.roots()).roots);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => { refresh(); }, []);

  // 轮询后台索引任务：运行中 1s，空闲 3s。任务结束（finished_at 变化）
  // 时记录该 root 的结果并通知外层刷新状态栏。
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      let running = false;
      try {
        const t = await api.indexStatus();
        running = t.running;
        if (!cancelled) {
          setTask(t);
          if (!t.running && t.finished_at && t.finished_at !== handledRef.current) {
            handledRef.current = t.finished_at;
            if (t.root) {
              if (t.result) {
                const st = t.result;
                setDoneStats(prev => ({ ...prev, [t.root]: st }));
              }
              if (t.error) {
                const err = t.error;
                setRowErrors(prev => ({ ...prev, [t.root]: err }));
              } else {
                setRowErrors(prev => ({ ...prev, [t.root]: "" }));
              }
              onChanged?.();
            }
            refresh();
          }
        }
      } catch { /* 边车未就绪，静默重试 */ }
      if (!cancelled) timer = setTimeout(poll, running ? 1000 : 3000);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, []);

  const addRoot = async () => {
    const sel = await open({ directory: true, multiple: false, title: "选择要加入索引的文件夹" });
    if (!sel || typeof sel !== "string") return;
    setBusy(true);
    try {
      await api.addRoot(sel);
      await refresh();
      await api.index(sel, false);   // 后台任务，立即返回
      onChanged?.();
    } catch (e: any) {
      alert("添加失败：" + String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const indexPath = async (path: string) => {
    try {
      await api.index(path, false);
    } catch (e: any) {
      setRowErrors(prev => ({ ...prev, [path]: String(e?.message ?? e) }));
    }
  };

  const removeRoot = async (path: string) => {
    if (!confirm(`从索引中移除？\n${path}\n（不会删除原文件）`)) return;
    try {
      await api.removeRoot(path);
      await refresh();
      onChanged?.();
    } catch (e: any) {
      alert("移除失败：" + String(e?.message ?? e));
    }
  };

  const reindexAll = async () => {
    if (roots.length === 0) return;
    setBusy(true);
    try {
      await api.reindexAll();   // 后台任务，立即返回
      onChanged?.();
    } catch (e: any) {
      alert("全量重建失败：" + String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const cancelTask = async () => {
    try { await api.indexCancel(); } catch (e) { console.error(e); }
  };

  return (
    <div className="root-manager">
      <div className="panel-header">
        <span>索引范围</span>
        <div>
          <button className="btn-sm" onClick={addRoot} disabled={busy}>+ 添加</button>
          <button className="btn-sm" onClick={reindexAll} disabled={busy || roots.length === 0}>↻ 重建</button>
        </div>
      </div>
      <div className="root-list">
        {roots.length === 0 && <div className="empty">（空）点「+ 添加」选一个文件夹开始</div>}
        {roots.map(path => {
          const isRunning = !!task?.running && (task.root === path || (task.kind === "index_paths" && path.startsWith(task.root)));
          const p = isRunning ? task!.progress : null;
          const pct = p && p.total ? Math.min(100, Math.round(100 * p.scanned / p.total)) : null;
          const last = doneStats[path];
          const err = rowErrors[path];
          return (
            <div key={path} className="root-row">
              <div className="root-main">
                <span className="root-path" title={path}>{path}</span>
                {isRunning ? (
                  <div className="root-progress">
                    <div className="root-progress-bar">
                      <div
                        className="root-progress-fill"
                        style={pct !== null ? { width: `${pct}%` } : { width: "8%", opacity: 0.6 }}
                      />
                    </div>
                    <span className="root-progress-text">
                      {pct !== null ? `${pct}% · ` : ""}{p?.scanned ?? 0} 文件
                      {p?.errors ? ` ⚠${p.errors}` : ""}
                      {task?.by === "watcher" ? " · 自动同步" : ""}
                      {task?.kind === "index_paths" ? " · 定向增量" : ""}
                    </span>
                  </div>
                ) : err ? (
                  <span className="root-status error" title={err}>✗ {err.slice(0, 60)}</span>
                ) : last ? (
                  <span className="root-status done" title={`扫描 ${last.scanned} · 新索引 ${last.indexed} · 跳过 ${last.skipped} · 错误 ${last.errors}`}>
                    {last.indexed} 文件 · {last.seconds.toFixed(1)}s{last.cancelled ? " · 已取消" : ""}
                  </span>
                ) : (
                  <span className="root-status">空闲</span>
                )}
              </div>
              {isRunning
                ? <button className="btn-xs" title="取消索引" onClick={cancelTask}>■</button>
                : <button className="btn-xs" title="重新索引" onClick={() => indexPath(path)} disabled={busy}>↻</button>}
              <button className="btn-xs" title="从索引移除" onClick={() => removeRoot(path)} disabled={busy}>×</button>
            </div>
          );
        })}
      </div>
    </div>
  );
}