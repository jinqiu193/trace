import { useEffect, useRef, useState } from "react";
import { api, SearchResult, VideoHit } from "../api";

const FILE_TYPE_COLORS: Record<string, string> = {
  image: "#e879f9",
  pdf: "#f97316",
  text: "#22d3ee",
  code: "#a3e635",
  audio: "#fbbf24",
  video: "#f43f5e",
};

const HISTORY_KEY = "trace_search_history";
const HISTORY_MAX = 20;
const PAGE_SIZE = 20;

function loadHistory(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(v) ? v.filter(x => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveHistory(q: string): string[] {
  const h = loadHistory().filter(x => x !== q);
  h.unshift(q);
  const out = h.slice(0, HISTORY_MAX);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(out)); } catch { /* 私隐模式 */ }
  return out;
}

export interface ImageSibling {
  path: string;
  name: string;
}

export interface SearchPanelProps {
  fileType?: string;
  dir?: string;
  threshold?: number;
  onPlayVideo?: (path: string, location: string, hits?: VideoHit[]) => void;
  onPreviewImage?: (path: string, siblings: ImageSibling[]) => void;
  onResultCount?: (n: number) => void;
}

export function SearchPanel({ fileType, dir, threshold, onPlayVideo, onPreviewImage, onResultCount }: SearchPanelProps = {}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [ms, setMs] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [fuzzy, setFuzzy] = useState(false);
  const [history, setHistory] = useState<string[]>(loadHistory);
  const [showHistory, setShowHistory] = useState(false);
  const [histSel, setHistSel] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => { onResultCount?.(results.length); }, [results]);

  // 视频命中 + 有 location → 走内嵌 HTML5 预览（跳到那一秒）
  // 图片 → 走内嵌图片预览（带翻页）；其他 → 系统默认应用
  const handleOpen = (r: SearchResult) => {
    if (r.file_type === "video" && r.location && onPlayVideo) {
      onPlayVideo(r.path, r.location, r.hits);
      return;
    }
    if (r.file_type === "image" && onPreviewImage) {
      const siblings: ImageSibling[] = results
        .filter(x => x.file_type === "image")
        .map(x => ({ path: x.path, name: x.name }));
      onPreviewImage(r.path, siblings);
      return;
    }
    api.openPath(r.path, r.location || undefined).catch(err => console.error(err));
  };

  const handleOpenAt = (path: string, location: string, hits?: VideoHit[]) => {
    if (onPlayVideo) {
      onPlayVideo(path, location, hits);
    } else {
      api.openPath(path, location).catch(err => console.error(err));
    }
  };

  // 防抖检索（PRD FR-6: 100ms）。limit 变化（加载更多）复用同一路径。
  useEffect(() => {
    if (!query.trim()) { setResults([]); setMs(null); setFuzzy(false); return; }
    const t = setTimeout(async () => {
      setLoading(true); setError(null);
      try {
        const r = await api.search(query, {
          limit,
          file_type: fileType || undefined,
          dir: dir || undefined,
          threshold: threshold && threshold > 0 ? threshold : undefined,
        });
        setResults(r.results);
        setMs(r.ms);
        setFuzzy(r.results.some(x => x.fuzzy_match));
        setSelected(0);
      } catch (e: any) {
        setError(String(e?.message ?? e));
      } finally {
        setLoading(false);
      }
    }, 150);
    return () => clearTimeout(t);
  }, [query, fileType, dir, threshold, limit]);

  // 历史记录：查询稳定 2s 后写入（避免打字中间态全部入史）
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) return;
    const t = setTimeout(() => setHistory(saveHistory(q)), 2000);
    return () => clearTimeout(t);
  }, [query]);

  const applyHistory = (q: string) => {
    setQuery(q);
    setShowHistory(false);
    setHistSel(-1);
    inputRef.current?.focus();
  };

  const removeHistory = (q: string) => {
    const h = history.filter(x => x !== q);
    setHistory(h);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(h)); } catch { /* noop */ }
  };

  // 键盘导航（FR-6: ↑↓ Enter）：
  // - 主输入框：↑↓ 导航历史下拉（展开时）或结果；Enter 打开
  // - 其他输入框（如目录过滤）：不拦截（用户在编辑文本）
  // - 非输入焦点（点击了结果区/空白处）：↑↓ Enter 仍可导航结果
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField = !!target && (
        target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable
      );
      if (inField && target !== inputRef.current) return;

      if (showHistory && e.target === inputRef.current && history.length > 0) {
        if (e.key === "ArrowDown") { e.preventDefault(); setHistSel(h => Math.min(h + 1, history.length - 1)); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setHistSel(h => Math.max(h - 1, 0)); return; }
        if (e.key === "Enter" && histSel >= 0) { e.preventDefault(); applyHistory(history[histSel]); return; }
        if (e.key === "Escape") { setShowHistory(false); setHistSel(-1); return; }
      }
      if (e.key === "ArrowDown") {
        e.preventDefault(); setShowHistory(false);
        setSelected(s => Math.min(s + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelected(s => Math.max(s - 1, 0));
      } else if (e.key === "Enter" && results[selected]) {
        e.preventDefault();
        handleOpen(results[selected]);
      } else if (e.key === "Escape") {
        setShowHistory(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [results, selected, showHistory, history, histSel]);

  // ---- 导出 ----
  const copyPaths = async () => {
    try {
      await navigator.clipboard.writeText(results.map(r => r.path).join("\n"));
    } catch (e) {
      console.error(e);
    }
  };
  const exportCSV = () => {
    const head = "name,path,type,location,score,z,cos,snippet";
    const esc = (x: unknown) => `"${String(x ?? "").replace(/"/g, '""')}"`;
    const rows = results.map(r =>
      [r.name, r.path, r.file_type, r.location, r.score, r.z, r.cos_sim,
       (r.snippet || "").replace(/[«»]/g, "")].map(esc).join(","));
    const blob = new Blob(["\ufeff" + head + "\n" + rows.join("\n")],
      { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `trace_results_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="search-panel">
      <div className="search-box">
        <span className="search-icon">🔍</span>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setShowHistory(true); setHistSel(-1); }}
          onFocus={() => setShowHistory(true)}
          onBlur={() => setTimeout(() => setShowHistory(false), 150)}
          placeholder='搜点什么？比如 "a penguin"、"讲睡眠的那段"、"invoice 1024"'
          spellCheck={false}
          autoComplete="off"
        />
        {loading && <span className="search-loading">…</span>}
        {ms !== null && <span className="search-ms">{ms.toFixed(0)}ms · {results.length} 条</span>}
      </div>

      {showHistory && query.trim() === "" && history.length > 0 && (
        <div className="search-history">
          <div className="search-history-head">
            <span>最近搜索</span>
            <button className="btn-xs" onClick={() => {
              setHistory([]);
              try { localStorage.removeItem(HISTORY_KEY); } catch { /* noop */ }
            }}>清空</button>
          </div>
          {history.map((h, i) => (
            <div
              key={h}
              className={"search-history-item" + (i === histSel ? " active" : "")}
              onMouseEnter={() => setHistSel(i)}
              onMouseLeave={() => setHistSel(-1)}
              onMouseDown={(e) => { e.preventDefault(); applyHistory(h); }}
            >
              <span className="search-history-text">{h}</span>
              <button
                className="search-history-del"
                onMouseDown={(e) => { e.preventDefault(); removeHistory(h); }}
                title="删除该条"
              >×</button>
            </div>
          ))}
        </div>
      )}

      {error && <div className="search-error">{error}</div>}

      {fuzzy && results.length > 0 && (
        <div className="fuzzy-note">未找到精确关键词命中，以下为模糊匹配结果（可能存在拼写偏差）</div>
      )}

      {results.length > 0 && (
        <div className="search-actions">
          <button className="btn-xs" onClick={copyPaths}>复制路径</button>
          <button className="btn-xs" onClick={exportCSV}>导出 CSV</button>
        </div>
      )}

      <div className="search-results">
        {results.length === 0 && query && !loading && !error && (
          <div className="empty">没有命中。试试换个说法，或点左侧「+ 添加」索引更多文件夹。</div>
        )}
        {results.map((r, i) => (
          <div
            key={r.path + i}
            className={"result" + (i === selected ? " selected" : "") + (r.strong ? "" : " weak")}
            onClick={() => handleOpen(r)}
            onDoubleClick={() => api.revealInFolder(r.path)}
            onMouseEnter={() => setSelected(i)}
          >
            <div className="result-body">
              {r.file_type === "image" && (
                <img
                  className="result-thumb"
                  src={api.imageSrc(r.path, 96)}
                  loading="lazy"
                  alt=""
                  onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                />
              )}
              <div className="result-content">
                <div className="result-row1">
                  <span className="result-type" style={{ background: FILE_TYPE_COLORS[r.file_type] ?? "#64748b" }}>
                    {r.file_type}
                  </span>
                  <span className="result-name">{r.name}</span>
                  {r.location && <span className="result-loc">@{r.location}</span>}
                  <span className="result-score">score {r.score.toFixed(3)}</span>
                  {r.confidence === "low" && <span className="confidence-low">⚠ 低置信</span>}
                  {r.confidence === "medium" && <span className="confidence-med">中置信</span>}
                </div>
                <div className="result-row2">
                  <span className="result-z">z={r.z.toFixed(2)}</span>
                  <span className="result-cos">cos={r.cos_sim.toFixed(3)}</span>
                  <span className="result-cov">cov={r.coverage.toFixed(2)}</span>
                  {!r.strong && <span className="result-weak-tag">弱匹配</span>}
                </div>
                {r.snippet && <div className="result-snippet">{r.snippet}</div>}
                {r.file_type === "video" && r.nearby_frames && r.nearby_frames.length > 0 && (
                  <div className="frame-strip">
                    {r.nearby_frames.map(f => (
                      <img
                        key={f.loc}
                        src={`${api.baseUrl()}/api/video/frame?path=${encodeURIComponent(r.path)}&t=${f.t.toFixed(2)}&max_w=160`}
                        alt={f.loc}
                        className={"frame-strip-thumb" + (f.loc === r.location ? " active" : "")}
                        onClick={(e) => { e.stopPropagation(); handleOpenAt(r.path, f.loc, r.hits); }}
                        title={`跳到 ${f.loc} 播放`}
                      />
                    ))}
                    <span className="frame-strip-hint">点击缩略图跳到那一秒</span>
                  </div>
                )}
                <div className="result-path">{r.path}</div>
              </div>
            </div>
          </div>
        ))}
        {results.length > 0 && results.length >= limit && (
          <button className="load-more" onClick={() => setLimit(l => l + PAGE_SIZE)}>
            加载更多（已显示 {results.length} 条）
          </button>
        )}
      </div>
    </div>
  );
}