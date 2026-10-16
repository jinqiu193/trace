import { useEffect, useRef, useState } from "react";
import { api } from "../api";

export interface ImagePreviewProps {
  path: string;
  /** 同一搜索结果里其他图片（用于 ←/→ 翻页） */
  siblings?: { path: string; name: string }[];
  onClose: () => void;
  onNavigate?: (path: string) => void;
}

const MIN_ZOOM = 0.05;
const MAX_ZOOM = 20;
const WHEEL_STEP = 1.15;       // 滚轮一次 1.15× 缩放
const BTN_STEP = 1.4;          // 按钮一次 1.4×

export function ImagePreview({ path, siblings, onClose, onNavigate }: ImagePreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  // 自然尺寸（onLoad 后填）
  const [natW, setNatW] = useState(0);
  const [natH, setNatH] = useState(0);
  // 视口尺寸（窗口 resize 时更新）
  const [vpW, setVpW] = useState(0);
  const [vpH, setVpH] = useState(0);
  // 变换
  const [zoom, setZoom] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  // 拖拽
  const draggingRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  // 加载态
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 上一张 / 下一张
  const idx = siblings ? siblings.findIndex(s => s.path === path) : -1;
  const hasPrev = siblings && idx > 0;
  const hasNext = siblings && idx >= 0 && idx < siblings.length - 1;

  const fileName = path.split(/[\\/]/).pop() ?? path;

  // ---- 视口尺寸 ----
  useEffect(() => {
    const update = () => {
      const el = containerRef.current;
      if (!el) return;
      setVpW(el.clientWidth);
      setVpH(el.clientHeight);
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  // ---- 加载完成时按"fit"算初始 zoom（首次进入） ----
  const fitOnceRef = useRef(false);
  useEffect(() => {
    fitOnceRef.current = false;
    setLoaded(false);
    setErr(null);
    setZoom(1); setTx(0); setTy(0);
  }, [path]);

  const handleImgLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setNatW(img.naturalWidth);
    setNatH(img.naturalHeight);
    setLoaded(true);
    if (!fitOnceRef.current && vpW > 0 && vpH > 0) {
      fitOnceRef.current = true;
      const z = Math.min(vpW / img.naturalWidth, vpH / img.naturalHeight, 1);
      setZoom(z);
      setTx(0); setTy(0);
    }
  };

  const handleImgErr = () => setErr("图片加载失败（可能已删除或权限不足）");

  // ---- 缩放（围绕光标） ----
  const zoomAt = (nextZoom: number, anchorX: number, anchorY: number) => {
    const z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, nextZoom));
    // 保持光标下的图像点不动：先求光标在"图像未变换"坐标系的位置，再变换
    const k = z / zoom;
    setTx(prev => anchorX - (anchorX - prev) * k);
    setTy(prev => anchorY - (anchorY - prev) * k);
    setZoom(z);
  };

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    const ax = e.clientX - rect.left - rect.width / 2 - tx;
    const ay = e.clientY - rect.top - rect.height / 2 - ty;
    const dir = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
    zoomAt(zoom * dir, ax, ay);
  };

  // ---- 拖拽 ----
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.target as Element).setPointerCapture(e.pointerId);
    draggingRef.current = { x: e.clientX, y: e.clientY, tx, ty };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = draggingRef.current;
    if (!d) return;
    setTx(d.tx + (e.clientX - d.x));
    setTy(d.ty + (e.clientY - d.y));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (draggingRef.current) {
      (e.target as Element).releasePointerCapture(e.pointerId);
      draggingRef.current = null;
    }
  };

  // ---- 缩放工具 ----
  const fitToWindow = () => {
    if (!natW || !vpW) return;
    const z = Math.min(vpW / natW, vpH / natH, 1);
    setZoom(z); setTx(0); setTy(0);
  };
  const oneToOne = () => {
    const rect = containerRef.current!.getBoundingClientRect();
    zoomAt(1, -tx + (rect.width - natW) / 2, -ty + (rect.height - natH) / 2);
    setTx((vpW - natW) / 2); setTy((vpH - natH) / 2);
  };

  // ---- 翻页 ----
  const goPrev = () => { if (hasPrev && onNavigate) onNavigate(siblings![idx - 1].path); };
  const goNext = () => { if (hasNext && onNavigate) onNavigate(siblings![idx + 1].path); };

  // ---- 键盘 ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      if (e.key === "ArrowLeft" && hasPrev) { e.preventDefault(); goPrev(); return; }
      if (e.key === "ArrowRight" && hasNext) { e.preventDefault(); goNext(); return; }
      if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        zoomAt(zoom * BTN_STEP, -tx, -ty);
        return;
      }
      if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        zoomAt(zoom / BTN_STEP, -tx, -ty);
        return;
      }
      if (e.key === "0") { e.preventDefault(); oneToOne(); return; }
      if (e.key === "f" || e.key === "F") { e.preventDefault(); fitToWindow(); return; }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, hasPrev, hasNext, zoom, tx, ty, natW, natH, vpW, vpH]);

  // 用后端限速接口，DPR 高的屏给 1.5× max_w 避免糊
  const dpr = window.devicePixelRatio || 1;
  const max_w = Math.max(1024, Math.round(Math.max(vpW, natW) * dpr * 0.9));
  const src = `${api.baseUrl()}/api/raw?path=${encodeURIComponent(path)}&max_w=${max_w}`;

  return (
    <div className="image-preview-overlay" onClick={onClose}>
      <div className="image-preview-window" onClick={e => e.stopPropagation()}>
        <div className="image-preview-header">
          <span className="image-preview-name" title={path}>{fileName}</span>
          {siblings && siblings.length > 1 && (
            <span className="image-preview-pos">{idx + 1}/{siblings.length}</span>
          )}
          {loaded && (
            <span className="image-preview-info">
              {natW}×{natH} · {(zoom * 100).toFixed(0)}%
            </span>
          )}
          <div className="image-preview-toolbar">
            <button onClick={(e) => { e.stopPropagation(); fitToWindow(); }} title="适应窗口 (F)">⤢</button>
            <button onClick={(e) => { e.stopPropagation(); oneToOne(); }} title="1:1 (0)">1:1</button>
            <button onClick={(e) => { e.stopPropagation(); zoomAt(zoom / BTN_STEP, -tx, -ty); }} title="缩小 (-)">−</button>
            <button onClick={(e) => { e.stopPropagation(); zoomAt(zoom * BTN_STEP, -tx, -ty); }} title="放大 (+)">+</button>
            {hasPrev && <button onClick={(e) => { e.stopPropagation(); goPrev(); }} title="上一张 (←)">‹</button>}
            {hasNext && <button onClick={(e) => { e.stopPropagation(); goNext(); }} title="下一张 (→)">›</button>}
            <button onClick={(e) => { e.stopPropagation(); api.revealInFolder(path); }} title="在文件管理器中显示">📂</button>
            <button className="image-preview-close" onClick={onClose} title="关闭 (Esc)">✕</button>
          </div>
        </div>

        <div
          ref={containerRef}
          className="image-preview-stage"
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onClick={e => e.stopPropagation()}
        >
          {!loaded && !err && <div className="image-preview-loading">加载中…</div>}
          {err && <div className="image-preview-err">{err}</div>}
          <img
            ref={imgRef}
            key={path}
            src={src}
            alt={fileName}
            draggable={false}
            onLoad={handleImgLoad}
            onError={handleImgErr}
            className={"image-preview-img" + (loaded ? "" : " hidden")}
            style={{
              transform: `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px)) scale(${zoom})`,
            }}
          />
        </div>

        <div className="image-preview-hint">
          滚轮 缩放  ·  拖动 平移  ·  +/- 缩放  ·  0 1:1  ·  F 适应窗口  ·  ← / → 翻页  ·  Esc 关闭
        </div>
      </div>
    </div>
  );
}
