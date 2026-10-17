import { useEffect, useRef, useState, type JSX } from "react";
import { api } from "../api";
import { IconClose, IconMusic, IconDocText, IconFolderOpen } from "../icons";

export interface FilePreviewProps {
  path: string;
  file_type: string;
  /** 音频命中位置（mm:ss） */
  location?: string;
  /** 文本高亮词（当前查询） */
  query?: string;
  onClose: () => void;
}

const MAX_RENDER_LINES = 3000;

function tsOf(loc?: string): number {
  if (!loc) return 0;
  const parts = loc.trim().split(":").map(Number);
  if (!parts.length || parts.some(x => Number.isNaN(x))) return 0;
  return parts.reduce((a, p) => a * 60 + p, 0);
}

function fmtBytes(n: number): string {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

/** 把文本按查询词切分为 高亮/普通 片段（查询词按空白拆分，长词优先） */
function splitHighlight(text: string, terms: string[]): { s: string; hit: boolean }[] {
  const ws = terms.filter(t => t.length >= 2).sort((a, b) => b.length - a.length);
  if (!ws.length) return [{ s: text, hit: false }];
  const re = new RegExp(`(${ws.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).filter(x => x !== "").map(seg => ({
    s: seg,
    hit: ws.some(w => seg.toLowerCase() === w.toLowerCase()),
  }));
}

export function FilePreview({ path, file_type, location, query, onClose }: FilePreviewProps) {
  const [text, setText] = useState<string | null>(null);
  const [textMeta, setTextMeta] = useState<{ encoding: string; truncated: boolean; size: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const firstHitRef = useRef<HTMLDivElement>(null);

  const fileName = path.split(/[\\/]/).pop() ?? path;

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 文本类：拉内容
  useEffect(() => {
    if (file_type !== "text" && file_type !== "code") return;
    let cancelled = false;
    setText(null); setErr(null); setTextMeta(null);
    api.textContent(path).then(r => {
      if (cancelled) return;
      setText(r.text);
      setTextMeta({ encoding: r.encoding, truncated: r.truncated, size: r.size });
    }).catch(e => {
      if (!cancelled) setErr(String(e?.message ?? e));
    });
    return () => { cancelled = true; };
  }, [path, file_type]);

  // 音频：跳到命中位置
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !location) return;
    const jump = () => {
      const t = tsOf(location);
      if (t > 0 && Number.isFinite(a.duration) && t < a.duration - 1) {
        a.currentTime = t;
      }
    };
    if (a.readyState >= 1) jump();
    else a.addEventListener("loadedmetadata", jump, { once: true });
  }, [location, text]);

  // 文本：滚动到首个命中行
  useEffect(() => {
    if (text === null) return;
    const t = setTimeout(() => firstHitRef.current?.scrollIntoView({ block: "center" }), 60);
    return () => clearTimeout(t);
  }, [text, query]);

  // ---- 分支渲染 ----
  let body: JSX.Element;
  let modeIcon: JSX.Element;

  if (file_type === "audio") {
    modeIcon = <IconMusic size={14} />;
    body = (
      <div className="fp-audio">
        <div className="fp-audio-card">
          <div className="fp-audio-icon"><IconMusic size={34} /></div>
          <div className="fp-audio-name" title={fileName}>{fileName}</div>
          {location && (
            <div className="fp-audio-loc">
              命中位置 <span className="fp-audio-loc-t">{location}</span>
              <button className="fp-audio-jump" onClick={() => {
                const a = audioRef.current;
                const t = tsOf(location);
                if (a && t > 0) { a.currentTime = Math.min(t, (a.duration || t) - 0.5); a.play().catch(() => undefined); }
              }}>▶ 从命中处播放</button>
            </div>
          )}
          <audio
            ref={audioRef}
            controls
            src={api.fileSrc(path)}
            onError={() => setErr("音频加载失败（格式不支持或文件已移动）")}
          />
        </div>
      </div>
    );
  } else if (file_type === "pdf") {
    modeIcon = <IconDocText size={14} />;
    body = (
      <embed
        className="fp-pdf"
        type="application/pdf"
        src={api.fileSrc(path)}
        onError={() => setErr("PDF 加载失败")}
      />
    );
  } else {
    modeIcon = <IconDocText size={14} />;
    const lines = text === null ? [] : text.replace(/\r\n/g, "\n").split("\n");
    const shown = lines.slice(0, MAX_RENDER_LINES);
    const terms = (query || "").split(/\s+/).filter(Boolean);
    body = (
      <div className="fp-text">
        {text === null && !err && (
          <div className="fp-loading"><span className="spinner" /> 加载中…</div>
        )}
        {err && <div className="fp-err">{err}</div>}
        {text !== null && (
          <>
            <div className="fp-text-meta">
              {textMeta?.encoding.toUpperCase()} · {lines.length.toLocaleString()} 行 · {fmtBytes(textMeta?.size ?? 0)}
              {textMeta?.truncated && <span className="fp-text-warn">（过大已截断）</span>}
              {terms.length > 0 && <span className="fp-text-hint"> · 命中词已高亮</span>}
            </div>
            <div className="fp-text-lines">
              {shown.map((ln, i) => {
                const segs = splitHighlight(ln, terms);
                const hasHit = segs.some(s => s.hit);
                return (
                  <div
                    key={i}
                    className={"fp-line" + (hasHit ? " hit" : "")}
                    ref={hasHit && !firstHitRef.current ? firstHitRef : undefined}
                  >
                    <span className="fp-ln">{i + 1}</span>
                    <span className="fp-lt">
                      {segs.map((s, j) => s.hit ? <mark key={j}>{s.s}</mark> : <span key={j}>{s.s}</span>)}
                      {ln === "" ? " " : null}
                    </span>
                  </div>
                );
              })}
            </div>
            {lines.length > MAX_RENDER_LINES && (
              <div className="fp-text-more">仅显示前 {MAX_RENDER_LINES.toLocaleString()} 行（共 {lines.length.toLocaleString()} 行）</div>
            )}
          </>
        )}
      </div>
    );
  }

  return (
    <div className="fp-overlay" onClick={onClose}>
      <div className="fp-window" onClick={e => e.stopPropagation()}>
        <div className="fp-header">
          {modeIcon}
          <span className="fp-name" title={path}>{fileName}</span>
          <span className="fp-type-tag">{file_type}</span>
          <div className="fp-toolbar">
            <button onClick={(e) => { e.stopPropagation(); api.revealInFolder(path); }} title="在文件管理器中显示">
              <IconFolderOpen size={13} />
            </button>
            <button className="fp-close" onClick={onClose} title="关闭 (Esc)"><IconClose size={14} /></button>
          </div>
        </div>
        <div className="fp-body">{body}</div>
        <div className="fp-hint">
          {file_type === "audio" && "播放器支持拖动进度 · Esc 关闭"}
          {file_type === "pdf" && "WebView2 原生 PDF 渲染 · Esc 关闭"}
          {(file_type === "text" || file_type === "code") && "查询词高亮 · 自动定位首个命中行 · Esc 关闭"}
        </div>
      </div>
    </div>
  );
}