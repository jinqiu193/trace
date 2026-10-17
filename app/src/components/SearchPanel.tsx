import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { api, SearchResult, SortKey, VideoHit } from "../api";
import {
  IconSearch, IconImage, IconClose, IconSimilar, IconList, IconGrid,
  IconClock, IconCopy, IconDownload, IconSparkle, IconUpload, IconKeyboard, IconCheck,
} from "../icons";

const FILE_TYPE_COLORS: Record<string, string> = {
  image: "#b2a0d8",
  pdf: "#cf9d6e",
  text: "#8fc4cd",
  code: "#a3bf8e",
  audio: "#ccb27e",
  video: "#d094a4",
};

const TYPE_LABELS: Record<string, string> = {
  image: "图片", pdf: "PDF", text: "文本", code: "代码", audio: "音频", video: "视频",
};

const FILTER_TYPES = ["text", "pdf", "image", "code", "audio", "video"] as const;

const SORTS: { key: SortKey; label: string }[] = [
  { key: "relevance", label: "相关度" },
  { key: "mtime_desc", label: "时间 新→旧" },
  { key: "mtime_asc", label: "时间 旧→新" },
  { key: "size_desc", label: "大小 大→小" },
  { key: "name", label: "文件名 A→Z" },
];

const DATE_RANGES: { key: string; label: string }[] = [
  { key: "", label: "任意时间" },
  { key: "today", label: "今天" },
  { key: "week", label: "近 7 天" },
  { key: "month", label: "近 30 天" },
  { key: "year", label: "近一年" },
];

const SIZE_RANGES: { key: string; label: string }[] = [
  { key: "", label: "任意大小" },
  { key: "lt10", label: "< 10MB" },
  { key: "10-100", label: "10MB – 100MB" },
  { key: "100-1024", label: "100MB – 1GB" },
  { key: "gt1024", label: "> 1GB" },
];

const MB = 1024 * 1024;

function dateRangeToAfter(r: string): number | undefined {
  const now = Date.now() / 1000;
  if (r === "today") return now - 86400;
  if (r === "week") return now - 7 * 86400;
  if (r === "month") return now - 30 * 86400;
  if (r === "year") return now - 365 * 86400;
  return undefined;
}

function sizeRangeToPair(r: string): { min?: number; max?: number } {
  switch (r) {
    case "lt10": return { max: 10 * MB };
    case "10-100": return { min: 10 * MB, max: 100 * MB };
    case "100-1024": return { min: 100 * MB, max: 1024 * MB };
    case "gt1024": return { min: 1024 * MB };
    default: return {};
  }
}

/** 后端 snippet 的命中词包在 «» 中，渲染成 <mark> */
function renderSnippet(s: string) {
  if (!s) return null;
  const parts = s.split(/«([^»]*)»/);
  return parts.map((p, i) => (i % 2 === 1
    ? <mark key={i} className="snippet-mark">{p}</mark>
    : <span key={i}>{p}</span>));
}

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

/** 是否有全屏弹层打开（Esc 应交给它们处理） */
function overlayOpen(): boolean {
  return !!document.querySelector(
    ".settings-overlay, .video-preview-overlay, .image-preview-overlay, .help-overlay");
}

export interface ImageSibling {
  path: string;
  name: string;
}

export interface SearchPanelProps {
  onPlayVideo?: (path: string, location: string, hits?: VideoHit[]) => void;
  onPreviewImage?: (path: string, siblings: ImageSibling[]) => void;
  onPreviewFile?: (path: string, file_type: string, location?: string, query?: string) => void;
  onShowHelp?: () => void;
}

export function SearchPanel({ onPlayVideo, onPreviewImage, onPreviewFile, onShowHelp }: SearchPanelProps = {}) {
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
  const [toast, setToast] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const selRef = useRef<HTMLDivElement | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // 筛选（工具栏内联，与排序同级）
  const [fileType, setFileType] = useState("");
  const [dir, setDir] = useState("");
  const [threshold, setThreshold] = useState(0);
  const [dateRange, setDateRange] = useState("");    // "" / today / week / month / year
  const [sizeRange, setSizeRange] = useState("");    // "" / lt10 / 10-100 / 100-1024 / gt1024

  // 以图搜图状态
  const [imgFile, setImgFile] = useState<File | null>(null);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const imgUrlRef = useRef<string | null>(null);

  // More like this：以某切片向量搜相似（全模态）
  const [similarFrom, setSimilarFrom] = useState<{ sid: number; name: string } | null>(null);

  // 排序 / 视图 / facets
  const [sort, setSort] = useState<SortKey>("relevance");
  const [view, setView] = useState<"list" | "grid">("list");
  const [facets, setFacets] = useState<Record<string, number>>({});
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => { inputRef.current?.focus(); }, []);


  // 键盘选中项滚动跟随（在视口边缘时平滑滚入）
  useEffect(() => {
    selRef.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 1800);
  }, []);

  // 视频命中 + 有 location → 走内嵌 HTML5 预览（跳到那一秒）
  // 图片 → 内嵌图片预览（带翻页）；音频/文本/PDF → 内嵌 FilePreview；其他 → 系统默认应用
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
    if ((r.file_type === "audio" || r.file_type === "pdf"
      || r.file_type === "text" || r.file_type === "code") && onPreviewFile) {
      const q = query.trim().toLowerCase().startsWith("name:")
        ? query.trim().slice(5).trim()      // name: 模式高亮文件名词
        : query.trim();
      onPreviewFile(r.path, r.file_type, r.location || undefined, q || undefined);
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
    setSimilarFrom(null);
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

  // ---- More like this ----
  const startSimilar = useCallback((sid: number, name: string) => {
    if (imgUrlRef.current) { URL.revokeObjectURL(imgUrlRef.current); imgUrlRef.current = null; }
    setImgFile(null);
    setImgUrl(null);
    setQuery("");
    setSimilarFrom({ sid, name });
  }, []);

  const clearSimilar = useCallback(() => {
    setSimilarFrom(null);
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

  // 统一检索入口，三模式：
  // - More like this：切片向量查询（最快，无嵌入）
  // - 图片（+可选补充文字 = 图+文联合查询）：视觉嵌入慢，300ms 防抖
  //   （打字改补充词时不每键重嵌）；图片模式下文字是联合条件而非切回文字搜索
  // - 文字：150ms 防抖。limit/sort 变化复用同一路径。
  useEffect(() => {
    if (similarFrom) {
      let cancelled = false;
      (async () => {
        setLoading(true); setError(null);
        try {
          const r = await api.searchSimilar(similarFrom.sid, {
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
    if (imgFile) {
      const text = query.trim();
      const t = setTimeout(async () => {
        setLoading(true); setError(null);
        try {
          const r = await api.searchImage(imgFile, {
            limit,
            sort,
            text: text || undefined,
            file_type: fileType || undefined,
            dir: dir || undefined,
          });
          setResults(r.results);
          setMs(r.ms);
          setFacets(r.facets ?? {});
          setFuzzy(false);
          setSelected(0);
        } catch (e: any) {
          setError(String(e?.message ?? e));
        } finally {
          setLoading(false);
        }
      }, 300);
      return () => clearTimeout(t);
    }
    if (!query.trim()) { setResults([]); setMs(null); setFuzzy(false); setFacets({}); return; }
    const t = setTimeout(async () => {
      setLoading(true); setError(null);
      try {
        const sz = sizeRangeToPair(sizeRange);
        const r = await api.search(query, {
          limit,
          sort,
          file_type: fileType || undefined,
          dir: dir || undefined,
          threshold: threshold && threshold > 0 ? threshold : undefined,
          mtime_after: dateRangeToAfter(dateRange),
          min_size: sz.min,
          max_size: sz.max,
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
  }, [similarFrom, imgFile, query, fileType, dir, threshold, dateRange, sizeRange, limit, sort]);

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

  // Esc 分层退出：历史下拉 → 查询文字 → 图片模式 → 相似模式
  const escapeBack = useCallback(() => {
    if (showHistory) { setShowHistory(false); setHistSel(-1); return true; }
    if (query) { setQuery(""); return true; }
    if (imgFile) { clearImage(); return true; }
    if (similarFrom) { clearSimilar(); return true; }
    return false;
  }, [showHistory, query, imgFile, similarFrom, clearImage, clearSimilar]);

  // 键盘导航 + 全局快捷键：
  // - ↑↓ 导航（历史下拉展开时优先历史，否则结果），Enter 打开
  // - Ctrl+K / `/` 聚焦搜索框（任意位置）
  // - Esc 分层退出（弹层打开时交给弹层）
  // - Ctrl+G 切换列表/网格；`?` 打开快捷键帮助
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField = !!target && (
        target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable
      );

      // Ctrl+K：任意位置聚焦搜索框
      if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
        return;
      }
      // Ctrl+G：切换视图
      if ((e.ctrlKey || e.metaKey) && (e.key === "g" || e.key === "G")) {
        e.preventDefault();
        setView(v => v === "list" ? "grid" : "list");
        return;
      }
      // `?` 打开帮助（非输入态）
      if (e.key === "?" && !inField) {
        e.preventDefault();
        onShowHelp?.();
        return;
      }
      // `/` 聚焦搜索框（非输入态）
      if (e.key === "/" && !inField) {
        e.preventDefault();
        inputRef.current?.focus();
        return;
      }

      // 以下仅搜索框自身或非输入焦点时生效
      if (inField && target !== inputRef.current) return;

      if (e.key === "Escape") {
        if (overlayOpen()) return;   // 弹层的 Esc 由弹层自己处理
        if (escapeBack()) e.preventDefault();
        return;
      }

      if (showHistory && e.target === inputRef.current && history.length > 0) {
        if (e.key === "ArrowDown") { e.preventDefault(); setHistSel(h => Math.min(h + 1, history.length - 1)); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setHistSel(h => Math.max(h - 1, 0)); return; }
        if (e.key === "Enter" && histSel >= 0) { e.preventDefault(); applyHistory(history[histSel]); return; }
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
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [results, selected, showHistory, history, histSel, escapeBack, onShowHelp]);

  // ---- 导出 ----
  const copyPaths = async () => {
    try {
      await navigator.clipboard.writeText(results.map(r => r.path).join("\n"));
      showToast(`已复制 ${results.length} 条路径`);
    } catch (e) {
      console.error(e);
      showToast("复制失败");
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
    showToast("CSV 已导出");
  };

  const active = !!imgFile || !!similarFrom || !!query.trim();
  // name: 前缀 → 文件名精确搜索模式（Everything 风格）
  const isNameMode = !imgFile && !similarFrom
    && query.trim().toLowerCase().startsWith("name:") && query.trim().length > 5;
  const hasFilters = fileType !== "" || dir !== "" || threshold > 0
    || dateRange !== "" || sizeRange !== "";

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
      <span className="grid-type-badge" style={{ "--type-c": FILE_TYPE_COLORS[r.file_type] ?? "#858c9e" } as CSSProperties}>
        {r.file_type}
      </span>
    );
  };

  return (
    <div className="search-panel">
      <div className="search-box">
        <span className="search-icon"><IconSearch size={16} /></span>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            if (similarFrom) clearSimilar();
            setQuery(e.target.value); setShowHistory(true); setHistSel(-1);
          }}
          onFocus={() => setShowHistory(true)}
          onBlur={() => setTimeout(() => setShowHistory(false), 150)}
          placeholder={imgFile
            ? "补充文字让结果更精准（如：夜景版本）· 清空 = 纯以图搜图"
            : similarFrom
              ? `正在以「${similarFrom.name}」搜相似 · 输入文字即退出`
              : '搜点什么？比如 "a penguin"、"讲睡眠的那段"、"invoice 1024"'}
          spellCheck={false}
          autoComplete="off"
        />
        {query && (
          <button className="clear-btn" onClick={() => { setQuery(""); inputRef.current?.focus(); }} title="清空搜索 (Esc)">
            <IconClose size={13} />
          </button>
        )}
        {imgUrl && (
          <img className="query-thumb" src={imgUrl} alt="" title={imgFile?.name} />
        )}
        {imgUrl && (
          <button className="icon-btn" onClick={clearImage} title="退出以图搜图">
            <IconClose size={13} />
          </button>
        )}
        <button
          className="img-pick-btn"
          onClick={() => fileInputRef.current?.click()}
          title="以图搜图：选择图片（也可直接拖拽图片到窗口，或 Ctrl+V 粘贴）"
        ><IconImage size={15} /></button>
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
        {loading && <span className="search-loading"><span className="spinner" /></span>}
        {ms !== null && !loading && <span className="search-ms">{ms.toFixed(0)}ms · {results.length} 条</span>}
        {!active && (
          <span className="search-ms" title="快捷键"><kbd className="kbd">Ctrl K</kbd></span>
        )}
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
              <IconClock size={13} />
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

      {/* 初始欢迎态 */}
      {!active && !loading && (
        <div className="welcome">
          <div className="welcome-logo"><IconSearch size={26} /></div>
          <div className="welcome-title">语义搜索本地文件</div>
          <div className="welcome-sub">
            文字描述、示例图片、甚至视频里的某句话——都能找到<br />
            中英文混合、拼写偏差也没关系 · 输入 <kbd className="kbd">name:关键词</kbd> 可直接按文件名精确搜索
          </div>
          <div className="welcome-cards">
            <div className="welcome-card">
              <span className="welcome-card-icon"><IconSearch size={15} /></span>
              <span className="welcome-card-title">自然语言查询</span>
              <span className="welcome-card-sub">直接描述要找的内容，按语义而不只是文件名匹配</span>
            </div>
            <div className="welcome-card">
              <span className="welcome-card-icon"><IconUpload size={15} /></span>
              <span className="welcome-card-title">以图搜图</span>
              <span className="welcome-card-sub">把图片拖进窗口或 <kbd className="kbd">Ctrl V</kbd> 粘贴，还能补一句文字精修结果</span>
            </div>
            <div className="welcome-card">
              <span className="welcome-card-icon"><IconKeyboard size={15} /></span>
              <span className="welcome-card-title">键盘优先</span>
              <span className="welcome-card-sub"><kbd className="kbd">Ctrl K</kbd> 聚焦 · <kbd className="kbd">↑↓</kbd> 选择 · <kbd className="kbd">Enter</kbd> 打开 · <kbd className="kbd">?</kbd> 全部快捷键</span>
            </div>
          </div>
        </div>
      )}

      {active && (
        <div className="search-toolbar">
          {isNameMode && (
            <span className="img-mode-tag name-mode-tag" title="文件名子串精确匹配，不走向量">
              <IconSearch size={12} />
              文件名搜索
            </span>
          )}
          {imgFile && (
            <span className="img-mode-tag">
              <IconImage size={12} />
              {query.trim() ? "图+文联合搜索" : "以图搜图 · 按相似度浏览"}
            </span>
          )}
          {similarFrom && (
            <span className="img-mode-tag" title={`切片 #${similarFrom.sid}`}>
              <IconSimilar size={12} />
              相似：{similarFrom.name}
              <button className="search-history-del" onClick={clearSimilar} title="退出相似搜索 (Esc)">
                <IconClose size={11} />
              </button>
            </span>
          )}
          <div className="toolbar-types">
            {FILTER_TYPES.map(t => (
              <button
                key={t}
                className={"type-btn" + (fileType === t ? " active" : "")}
                onClick={() => setFileType(fileType === t ? "" : t)}
                title={facets && facets[t] > 0 ? `候选池中有 ${facets[t]} 个` : "候选池为空"}
              >
                <span className="type-dot" style={{ background: FILE_TYPE_COLORS[t] }} />
                {TYPE_LABELS[t]}
                {facets && facets[t] > 0 && (
                  <span className="type-count">{facets[t]}</span>
                )}
              </button>
            ))}
          </div>
          <input
            className="dir-input"
            value={dir}
            onChange={(e) => setDir(e.target.value)}
            placeholder="目录包含…"
            spellCheck={false}
            title="按路径子串过滤结果"
          />
          <select
            className={"range-select" + (dateRange ? " active" : "")}
            value={dateRange}
            onChange={(e) => setDateRange(e.target.value)}
            title="按修改时间过滤"
          >
            {DATE_RANGES.map(d => <option key={d.key} value={d.key}>{d.label}</option>)}
          </select>
          <select
            className={"range-select" + (sizeRange ? " active" : "")}
            value={sizeRange}
            onChange={(e) => setSizeRange(e.target.value)}
            title="按文件大小过滤"
          >
            {SIZE_RANGES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          {!(imgFile || similarFrom || isNameMode) && (
            <div className="thr-wrap" title="相似度阈值（z 分），过滤弱匹配">
              <span className="thr-label">相似度</span>
              <input
                type="range"
                min={0}
                max={10}
                step={0.1}
                value={threshold}
                onChange={(e) => setThreshold(parseFloat(e.target.value))}
              />
              <span className="threshold-val">{threshold.toFixed(1)}</span>
            </div>
          )}
          {hasFilters && (
            <button
              className="filters-reset"
              onClick={() => { setFileType(""); setDir(""); setThreshold(0); setDateRange(""); setSizeRange(""); }}
              title="清除全部筛选条件"
            >✕ 重置</button>
          )}
          <span className="toolbar-spacer" />
          <label className="sort-label">排序</label>
          <select className="sort-select" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            {SORTS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <button
            className={"view-btn" + (view === "list" ? " active" : "")}
            onClick={() => setView("list")}
            title="列表视图 (Ctrl G)"
          ><IconList size={14} /></button>
          <button
            className={"view-btn" + (view === "grid" ? " active" : "")}
            onClick={() => setView("grid")}
            title="网格视图（适合浏览图片/视频）(Ctrl G)"
          ><IconGrid size={14} /></button>
        </div>
      )}

      {results.length > 0 && (
        <div className="search-actions">
          <button className="btn-xs" onClick={copyPaths}><IconCopy size={12} /> 复制路径</button>
          <button className="btn-xs" onClick={exportCSV}><IconDownload size={12} /> 导出 CSV</button>
        </div>
      )}

      <div className="search-results">
        {results.length === 0 && active && !loading && !error && (
          <div className="empty">
            {imgFile
              ? "没有找到相似内容。试试换一张图，或清除类型/目录过滤。"
              : similarFrom
                ? "没有找到相似内容。"
                : "没有命中。试试换个说法，或在右上角「设置」里添加更多文件夹。"}
          </div>
        )}

        {/* 加载骨架屏：首次查询无结果可展示时 */}
        {loading && results.length === 0 && active && (
          <>
            {[0, 1, 2, 3, 4].map(i => (
              <div key={i} className="skeleton-card">
                <div className="skeleton-thumb" />
                <div className="skeleton-lines">
                  <div className="skeleton-line w-60" />
                  <div className="skeleton-line w-80" />
                  <div className="skeleton-line w-40" />
                </div>
              </div>
            ))}
          </>
        )}

        {view === "grid" && results.length > 0 && (
          <div className="grid-results">
            {results.map((r, i) => (
              <div
                key={r.path + i}
                ref={i === selected ? selRef : undefined}
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
                  {r.sid != null && (
                    <button className="mlt-btn" title="搜索与此相似的内容"
                      onClick={(e) => { e.stopPropagation(); startSimilar(r.sid!, r.name); }}><IconSimilar size={11} /></button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {view === "list" && results.map((r, i) => (
          <div
            key={r.path + i}
            ref={i === selected ? selRef : undefined}
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
                  <span className="result-type" style={{ "--type-c": FILE_TYPE_COLORS[r.file_type] ?? "#858c9e" } as CSSProperties}>
                    {TYPE_LABELS[r.file_type] ?? r.file_type}
                  </span>
                  <span className="result-name">{r.name}</span>
                  {r.location && <span className="result-loc">@{r.location}</span>}
                  <span className="score-bar" title={`综合分 ${r.score.toFixed(3)}`}>
                    <i style={{ width: `${Math.max(0, Math.min(1, r.score)) * 100}%` }} />
                  </span>
                  <span className="result-score">{r.score.toFixed(3)}</span>
                  {r.confidence === "low" && <span className="confidence-low">⚠ 低置信</span>}
                  {r.confidence === "medium" && <span className="confidence-med">中置信</span>}
                  {r.sid != null && (
                    <button className="mlt-btn" title="搜索与此相似的内容"
                      onClick={(e) => { e.stopPropagation(); startSimilar(r.sid!, r.name); }}>
                      <IconSimilar size={11} /> 相似
                    </button>
                  )}
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
                {r.snippet && <div className="result-snippet">{renderSnippet(r.snippet)}</div>}
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
          <div className="drop-overlay-card">
            <IconSparkle size={18} />
            松手用这张图片搜索
          </div>
        </div>
      )}

      {toast && (
        <div className="toast">
          <IconCheck size={14} />
          {toast}
        </div>
      )}
    </div>
  );
}