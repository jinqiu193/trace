import { useCallback, useEffect, useRef, useState } from "react";
import { api, SearchResult, SortKey, VideoHit } from "../api";

const FILE_TYPE_COLORS: Record<string, string> = {
  image: "#e879f9",
  pdf: "#f97316",
  text: "#22d3ee",
  code: "#a3e635",
  audio: "#fbbf24",
  video: "#f43f5e",
};

const SORTS: { key: SortKey; label: string }[] = [
  { key: "relevance", label: "相关度" },
  { key: "mtime_desc", label: "时间 新→旧" },
  { key: "mtime_asc", label: "时间 旧→新" },
  { key: "size_desc", label: "大小 大→小" },
  { key: "name", label: "文件名 A→Z" },
];

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

function fmtSize(n?: number): string {
  if (!n || n <= 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v >= 100 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

function fmtDate(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function tsOf(loc?: string): number {
  if (!loc) return 0;
  const parts = loc.trim().split(":").map(Number);
  if (!parts.length || parts.some(x => Number.isNaN(x))) return 0;
  return parts.reduce((a, p) => a * 60 + p, 0);
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
  onFacets?: (f: Record<string, number>) => void;
}

export function SearchPanel({ fileType, dir, threshold, onPlayVideo, onPreviewImage, onResultCount, onFacets }: SearchPanelProps = {}) {
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
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 以图搜图状态
  const [imgFile, setImgFile] = useState<File | null>(null);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const imgUrlRef = useRef<string | null>(null);

  // 排序 / 视图 / facets
  const [sort, setSort] = useState<SortKey>("relevance");
  const [view, setView] = useState<"list" | "grid">("list");
  const [facets, setFacets] = useState<Record<string, number>>({});
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => { onResultCount?.(results.length); }, [results]);

  useEffect(() => { onFacets?.(facets); }, [facets]);

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

  // ---- 以图搜图：选图 / 清除 ----
  const pickImage = useCallback((f: File) => {
    if (!f.type.startsWith("image/")) return;
    if (imgUrlRef.current) URL.revokeObjectURL(imgUrlRef.current);
    const u = URL.createObjectURL(f);
    imgUrlRef.current = u;
    setImgFile(f);
    setImgUrl(u);
    setQuery("");
    setShowHistory(false);
    setHistSel(-1);
  }, []);

  const clearImage = useCallback(() => {
    if (imgUrlRef.current) { URL.revokeObjectURL(imgUrlRef.current); imgUrlRef.current = null; }
    setImgFile(null);
    setImgUrl(null);
    inputRef.current?.focus();
  }, []);

  // 全窗口拖拽图片 → 以图搜图
  useEffect(() => {
    const onDragOver = (e: DragEvent) => {
      if (e.dataTransfer?.types?.includes("Files")) {
        e.preventDefault();
        setDragOver(true);
      }
    };
    const onDragLeave = (e: DragEvent) => {
      if (!e.relatedTarget) setDragOver(false);
    };
    const onDrop = (e: DragEvent) => {
      setDragOver(false);
      const f = e.dataTransfer?.files?.[0];
      if (f) {
        e.preventDefault();
        pickImage(f);
      }
    };
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [pickImage]);

  // Ctrl+V 粘贴图片 → 以图搜图
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const f = Array.from(e.clipboardData?.files ?? []).find(x => x.type.startsWith("image/"));
      if (f) {
        e.preventDefault();
        pickImage(f);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [pickImage]);

  // 统一检索入口：图片模式立即执行（嵌入慢，无防抖）；文字模式防抖 150ms。
  // limit 变化（加载更多）与 sort 切换复用同一路径（后端排序在候选池内生效）。
  useEffect(() => {
    if (imgFile) {
      let cancelled = false;
      (async () => {
        setLoading(true); setError(null);
        try {
          const r = await api.searchImage(imgFile, {
            limit,
            sort,
            file_type: fileType || undefined,
            dir: dir || undefined,
          });
          if (cancelled) return;
          setResults(r.results);
          setMs(r.ms);
          setFacets(r.facets ?? {});
          setFuzzy(false);
          setSelected(0);
        } catch (e: any) {
          if (!cancelled) setError(String(e?.message ?? e));
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
      return () => { cancelled = true; };
    }
    if (!query.trim()) { setResults([]); setMs(null); setFuzzy(false); setFacets({}); return; }
    const t = setTimeout(async () => {
      setLoading(true); setError(null);
      try {
        const r = await api.search(query, {
          limit,
          sort,
          file_type: fileType || undefined,
          dir: dir || undefined,
          threshold: threshold && threshold > 0 ? threshold : undefined,
        });
        setResults(r.results);
        setMs(r.ms);
        setFacets(r.facets ?? {});
        setFuzzy(r.results.some(x => x.fuzzy_match));
        setSelected(0);
      } catch (e: any) {
        setError(String(e?.message ?? e));
      } finally {
        setLoading(false);
      }
    }, 150);
    return () => clearTimeout(t);
  }, [imgFile, query, fileType, dir, threshold, limit, sort]);

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
    const head = "name,path,type,location,score,z,cos,mtime,size,snippet";
    const esc = (x: unknown) => `"${String(x ?? "").replace(/"/g, '""')}"`;
    const rows = results.map(r =>
      [r.name, r.path, r.file_type, r.location, r.score, r.z, r.cos_sim,
        fmtDate(r.mtime), fmtSize(r.size),
        (r.snippet || "").replace(/[«»]/g, "")].map(esc).join(","));
    const blob = new Blob(["\ufeff" + head + "\n" + rows.join("\n")],
      { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `trace_results_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const active = !!imgFile || !!query.trim();

  const renderThumb = (r: SearchResult, maxW: number) => {
    if (r.file_type === "image") {
      return <img className="grid-thumb" src={api.imageSrc(r.path, maxW)} loading="lazy" alt=""
        onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />;
    }
    if (r.file_type === "video") {
      return <img className="grid-thumb"
        src={`${api.baseUrl()}/api/video/frame?path=${encodeURIComponent(r.path)}&t=${tsOf(r.location).toFixed(2)}&max_w=${maxW}`}
        loading="lazy" alt=""
        onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />;
    }
    return (
      <span className="grid-type-badge" style={{ background: FILE_TYPE_COLORS[r.file_type] ?? "#64748b" }}>
        {r.file_type}
      </span>
    );
  };

  return (
    <div className="search-panel">
      <div className="search-box">
        <span className="search-icon">🔍</span>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            if (imgFile) clearImage();
            setQuery(e.target.value); setShowHistory(true); setHistSel(-1);
          }}
          onFocus={() => setShowHistory(true)}
          onBlur={() => setTimeout(() => setShowHistory(false), 150)}
          placeholder={imgFile
            ? "以图搜图中 · 输入文字即切回文字搜索"
            : '搜点什么？比如 "a penguin"、"讲睡眠的那段"、"invoice 1024"'}
          spellCheck={false}
          autoComplete="off"
        />
        {imgUrl && (
          <img className="query-thumb" src={imgUrl} alt="" title={imgFile?.name} />
        )}
        {imgUrl && (
          <button className="query-thumb-clear" onClick={clearImage} title="退出以图搜图">✕</button>
        )}
        <button
          className="img-pick-btn"
          onClick={() => fileInputRef.current?.click()}
          title="以图搜图：选择图片（也可直接拖拽图片到窗口，或 Ctrl+V 粘贴）"
        >📷</button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) pickImage(f);
            e.target.value = "";
          }}
        />
        {loading && <span className="search-loading">{imgFile ? "分析中…" : "…"}</span>}
        {ms !== null && <span className="search-ms">{ms.toFixed(0)}ms · {results.length} 条</span>}
      </div>

      {showHistory && query.trim() === "" && !imgFile && history.length > 0 && (
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

      {active && (
        <div className="search-toolbar">
          {imgFile && <span className="img-mode-tag">🖼 以图搜图 · 按相似度浏览</span>}
          <span className="toolbar-spacer" />
          <label className="sort-label">排序</label>
          <select className="sort-select" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            {SORTS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <button
            className={"view-btn" + (view === "list" ? " active" : "")}
            onClick={() => setView("list")}
            title="列表视图"
          >☰</button>
          <button
            className={"view-btn" + (view === "grid" ? " active" : "")}
            onClick={() => setView("grid")}
            title="网格视图（适合浏览图片/视频）"
          >▦</button>
        </div>
      )}

      {results.length > 0 && (
        <div className="search-actions">
          <button className="btn-xs" onClick={copyPaths}>复制路径</button>
          <button className="btn-xs" onClick={exportCSV}>导出 CSV</button>
        </div>
      )}

      <div className="search-results">
        {results.length === 0 && active && !loading && !error && (
          <div className="empty">
            {imgFile
              ? "没有找到相似内容。试试换一张图，或清除类型/目录过滤。"
              : "没有命中。试试换个说法，或在右上角「⚙ 设置」里添加更多文件夹。"}
          </div>
        )}

        {view === "grid" && results.length > 0 && (
          <div className="grid-results">
            {results.map((r, i) => (
              <div
                key={r.path + i}
                className={"grid-card" + (i === selected ? " selected" : "") + (r.strong ? "" : " weak")}
                onClick={() => handleOpen(r)}
                onDoubleClick={() => api.revealInFolder(r.path)}
                onMouseEnter={() => setSelected(i)}
              >
                <div className="grid-thumb-wrap">
                  {renderThumb(r, 400)}
                  {r.location && <span className="grid-loc">@{r.location}</span>}
                </div>
                <div className="grid-name" title={r.path}>{r.name}</div>
                <div className="grid-meta">
                  <span className="grid-score">{r.score.toFixed(3)}</span>
                  {fmtSize(r.size) && <span>{fmtSize(r.size)}</span>}
                  {fmtDate(r.mtime) && <span>{fmtDate(r.mtime)}</span>}
                </div>
              </div>
            ))}
          </div>
        )}

        {view === "list" && results.map((r, i) => (
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
                  <span className="result-mtime" title="修改时间 · 大小">
                    {fmtDate(r.mtime)}{fmtSize(r.size) ? ` · ${fmtSize(r.size)}` : ""}
                  </span>
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

      {dragOver && (
        <div className="drop-overlay">
          <div className="drop-overlay-card">📥 松手用这张图片搜索</div>
        </div>
      )}
    </div>
  );
}