import { useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, IndexStats, IndexTaskStatus } from "../api";
import { IconPlus, IconRefresh, IconTrash, IconStop } from "../icons";
import { ConfirmDialog } from "./ConfirmDialog";

export function RootManager({ onChanged }: { onChanged?: () => void }) {
  const [roots, setRoots] = useState<string[]>([]);
  const [doneStats, setDoneStats] = useState<Record<string, IndexStats>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [task, setTask] = useState<IndexTaskStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const handledRef = useRef<string | null>(null);
  // 确认对话框（不用 window.confirm：WebView2 不接管 JS 对话框，恒返回 false）
  const [confirmRoot, setConfirmRoot] = useState<string | null>(null);
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    setConfirmRoot(path);
  };

  const doRemoveRoot = async (path: string) => {
    setConfirmRoot(null);
    try {
      await api.removeRoot(path);
      await refresh();
      onChanged?.();
    } catch (e: any) {
      setError("移除失败：" + String(e?.message ?? e));
    }
  };

  const reindexAll = async (full = false) => {
    if (roots.length === 0) return;
    if (full) { setConfirmRebuild(true); return; }
    setBusy(true);
    try {
      await api.reindexAll(false);   // 后台任务，立即返回（watcher 自动任务会被抢占）
      onChanged?.();
    } catch (e: any) {
      setError("刷新失败：" + String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const doRebuildAll = async () => {
    setConfirmRebuild(false);
    setBusy(true);
    try {
      await api.reindexAll(true);
      onChanged?.();
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      setError(msg.includes("409")
        ? "已有索引任务进行中，请等它完成，或先点「取消任务」再重建。"
        : "全量重建失败：" + msg);
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
          <button className="btn-sm" onClick={addRoot} disabled={busy}><IconPlus size={12} /> 添加</button>
          <button className="btn-sm" onClick={() => reindexAll(false)} disabled={busy || roots.length === 0}
            title="增量同步：只索引新增/修改的文件（秒级）"><IconRefresh size={12} /> 刷新</button>
          <button className="btn-sm" onClick={() => reindexAll(true)} disabled={busy || roots.length === 0}
            title="全量重建：所有文件重新切片+嵌入（慢，一般不需要）">全量重建</button>
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
                      {p?.current ? ` · ${p.current.split(/[\\/]/).pop()}` : ""}
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
                ? <button className="btn-xs" title="取消索引" onClick={cancelTask}><IconStop size={12} /></button>
                : <button className="btn-xs" title="增量同步此目录（只索引新增/修改的文件）" onClick={() => indexPath(path)} disabled={busy}><IconRefresh size={12} /></button>}
              <button className="btn-xs" title="从索引移除" onClick={() => removeRoot(path)} disabled={busy}><IconTrash size={12} /></button>
            </div>
          );
        })}
      </div>
      {error && <div className="root-error">{error}</div>}

      {confirmRoot && (
        <ConfirmDialog
          title="从索引中移除？"
          message={<>索引里会删掉 <code>{confirmRoot}</code> 的全部条目。<br />不会删除磁盘上的原文件。</>}
          confirmText="移除"
          danger
          onConfirm={() => doRemoveRoot(confirmRoot)}
          onCancel={() => setConfirmRoot(null)}
        />
      )}
      {confirmRebuild && (
        <ConfirmDialog
          title="全量重建？"
          message={<>所有文件会重新切片 + 重新嵌入（大视频/音频可能要很久）。<br />通常只需「刷新」即可同步新增/修改的文件。</>}
          confirmText="全量重建"
          danger
          onConfirm={doRebuildAll}
          onCancel={() => setConfirmRebuild(false)}
        />
      )}
    </div>
  );
}