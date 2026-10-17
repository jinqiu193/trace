import { useEffect, useState } from "react";
import { api, DuplicatesResponse } from "../api";
import { IconSearch, IconClose, IconSettings } from "../icons";

function fmtSize(n: number): string {
  if (!n || n <= 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v >= 100 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

/** 近似重复图片检测：image 切片 cos ≥ 阈值聚簇（Union-Find）。
 *  只读报告：点击打开原文件，双击定位到文件夹。 */
export function DuplicatesPanel() {
  const [loading, setLoading] = useState(false);
  const [threshold, setThreshold] = useState(0.95);
  const [report, setReport] = useState<DuplicatesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setError(null);
    try {
      setReport(await api.duplicates(threshold));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dup-panel">
      <div className="panel-header">
        <span>重复图片检测</span>
        <div className="dup-controls">
          <span className="dup-th-label">相似度 ≥ {threshold.toFixed(2)}</span>
          <input
            type="range" min={0.85} max={0.99} step={0.01}
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))}
            title="调低会找出更多「长得像」的图，调高只找几乎一致的"
          />
          <button className="btn-sm" onClick={run} disabled={loading}>
            {loading ? <span className="spinner" style={{ width: 11, height: 11 }} /> : <IconSearch size={12} />}
            {loading ? "检测中…" : "查找重复"}
          </button>
        </div>
      </div>
      {error && <div className="search-error">{error}</div>}
      {report && (
        <div className="dup-report">
          {report.cluster_count === 0 ? (
            <div className="empty">没有发现重复图片（阈值 {report.threshold}）。可调低阈值再试。</div>
          ) : (
            <>
              <div className="dup-summary">
                发现 <b>{report.cluster_count}</b> 组重复，共 <b>{report.file_count}</b> 个文件 · {report.ms.toFixed(0)}ms
              </div>
              {report.clusters.map((c, i) => (
                <div key={i} className="dup-cluster">
                  <div className="dup-cluster-head">
                    第 {i + 1} 组 · {c.members.length} 张 · 最高相似 {c.max_cos.toFixed(3)}
                  </div>
                  <div className="dup-members">
                    {c.members.map(m => (
                      <div key={m.path} className="dup-member" title={m.path}>
                        <img
                          src={api.imageSrc(m.path, 120)}
                          loading="lazy"
                          alt={m.name}
                          onClick={() => api.openPath(m.path).catch(console.error)}
                          onDoubleClick={() => api.revealInFolder(m.path).catch(console.error)}
                        />
                        <div className="dup-member-name">{m.name}</div>
                        <div className="dup-member-meta">
                          {m.cos >= 0.999 ? "原始" : `相似 ${m.cos.toFixed(3)}`}
                          {fmtSize(m.size) ? ` · ${fmtSize(m.size)}` : ""}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function SettingsModal({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="settings-overlay"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="settings-window">
        <div className="settings-header">
          <IconSettings size={14} />
          <span>设置</span>
          <button className="settings-close" onClick={onClose} title="关闭 (Esc)"><IconClose size={13} /></button>
        </div>
        <div className="settings-body">
          {children}
          <DuplicatesPanel />
        </div>
      </div>
    </div>
  );
}