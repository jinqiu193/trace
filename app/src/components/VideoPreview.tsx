import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, VideoHit } from "../api";
import { IconClose } from "../icons";

export interface VideoPreviewProps {
  path: string;
  location: string; // "HH:MM:SS"
  hits?: VideoHit[];
  onClose: () => void;
}

function tsToSeconds(s: string): number {
  const parts = s.split(":").map(p => parseInt(p, 10) || 0);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] || 0;
}

function fmtTime(t: number): string {
  if (!isFinite(t) || t < 0) t = 0;
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = Math.floor(t % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

const SEG_S = 90;                  // 一次从匹配点往后剪 90 秒（播完自动续）
const FRAME_FALLBACK_COUNT = 12;
const FRAME_FALLBACK_SPAN_S = 12;

export function VideoPreview({ path, location, hits, onClose }: VideoPreviewProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [mode, setMode] = useState<"video" | "frames" | "error">("video");

  const [info, setInfo] = useState<{ duration_s: number; fps: number; width: number; height: number } | null>(null);
  const [segT, setSegT] = useState(tsToSeconds(location)); // 当前流片段起点（全片坐标）
  const [posT, setPosT] = useState(segT);                  // 当前播放位置（全片坐标）
  const [frameIdx, setFrameIdx] = useState(0);
  const [frameTimestamps, setFrameTimestamps] = useState<number[]>([]);

  const targetSec = tsToSeconds(location);
  const baseUrl = api.baseUrl();
  const duration = info?.duration_s ?? 0;
  const frameT = (i: number) => frameTimestamps[i] ?? targetSec;

  const hitsSorted = useMemo(
    () => [...(hits ?? [])].sort((a, b) => a.t - b.t),
    [hits],
  );

  // ---- 视频元信息（时长 / 分辨率）----
  useEffect(() => {
    fetch(`${baseUrl}/api/video/info?path=${encodeURIComponent(path)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(j => {
        if (j && j.duration_s > 0) {
          setInfo({ duration_s: j.duration_s, fps: j.fps ?? 0, width: j.width ?? 0, height: j.height ?? 0 });
        }
      })
      .catch(() => { /* 元信息拿不到也能播，只是没有全片时间轴 */ });
  }, [baseUrl, path]);

  // ---- 视频模式：ffmpeg 流（WebM/VP9）从 segT 起播，播完自动续下一段 ----
  useEffect(() => {
    if (mode !== "video") return;
    const v = videoRef.current;
    if (!v) return;
    const onLoaded = () => {
      // 流本身就是从 segT 剪出来的，currentTime 0 即命中点
      v.play().catch(() => {/* 用户可能没点 */});
    };
    const onTime = () => setPosT(segT + v.currentTime);
    const onEnded = () => {
      const next = segT + SEG_S;
      if (duration && next < duration - 0.5) {
        setSegT(next); // 续播下一段（src 更新 → 重新加载 → onLoaded 自动 play）
      }
    };
    const onError = () => {
      // 流失败：降级到帧序列
      setMode("frames");
      const half = (FRAME_FALLBACK_COUNT - 1) / 2;
      const ts: number[] = [];
      for (let i = 0; i < FRAME_FALLBACK_COUNT; i++) {
        const offset = (i - half) * (FRAME_FALLBACK_SPAN_S / FRAME_FALLBACK_COUNT);
        ts.push(Math.max(0, targetSec + offset));
      }
      setFrameTimestamps(ts);
      setFrameIdx(Math.floor(half));
    };
    v.addEventListener("loadedmetadata", onLoaded);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("ended", onEnded);
    v.addEventListener("error", onError);
    return () => {
      v.removeEventListener("loadedmetadata", onLoaded);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("ended", onEnded);
      v.removeEventListener("error", onError);
    };
  }, [mode, segT, duration, targetSec]);

  // ---- 跳到全片任意时间（点击时间轴 / 命中片段 / ←→）----
  const seekTo = useCallback((t: number) => {
    const clamped = duration > 0 ? Math.max(0, Math.min(t, duration - 0.5)) : Math.max(0, t);
    setSegT(Math.floor(clamped));
    setPosT(clamped);
  }, [duration]);

  // ---- 键盘 ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (mode === "video" && e.key === " " && videoRef.current) {
        e.preventDefault();
        if (videoRef.current.paused) videoRef.current.play();
        else videoRef.current.pause();
      }
      if (mode === "video") {
        if (e.key === "ArrowRight") { e.preventDefault(); seekTo(posT + 5); }
        if (e.key === "ArrowLeft") { e.preventDefault(); seekTo(posT - 5); }
      }
      if (mode === "frames") {
        if (e.key === "ArrowRight") { e.preventDefault(); setFrameIdx(i => Math.min(i + 1, FRAME_FALLBACK_COUNT - 1)); }
        if (e.key === "ArrowLeft") { e.preventDefault(); setFrameIdx(i => Math.max(i - 1, 0)); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, mode, posT, seekTo]);

  // 视频 src：后端 ffmpeg 流（WebM/VP9），t 随时间轴点击变化
  const streamUrl = `${baseUrl}/api/video/stream?path=${encodeURIComponent(path)}&t=${segT.toFixed(2)}&dur=${SEG_S}`;

  const pct = (t: number) => (duration > 0 ? Math.min(100, Math.max(0, (t / duration) * 100)) : 0);

  return (
    <div className="video-preview-overlay" onClick={onClose}>
      <div className="video-preview-window vb-window" onClick={e => e.stopPropagation()}>
        <div className="video-preview-header">
          <span className="video-preview-loc">@{location}（{targetSec}s）</span>
          <span className="video-preview-name">{path.split(/[\\/]/).pop()}</span>
          {mode === "frames" && <span className="video-preview-mode-tag">帧回退</span>}
          <button className="video-preview-close" onClick={onClose} title="关闭（Esc）"><IconClose size={14} /></button>
        </div>
        <div className="vb-layout">
          <div className="vb-main">
            {mode === "video" && (
              <>
                <video
                  ref={videoRef}
                  src={streamUrl}
                  controls
                  autoPlay
                  muted={false}
                  preload="auto"
                  className="video-preview-player"
                />
                {/* 全片时间轴：命中点标记 + 播放位置 + 点击/拖动跳转 */}
                {duration > 0 && (
                  <div
                    className="vb-timeline"
                    title="点击跳转到全片任意位置"
                    onClick={e => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
                      seekTo(ratio * duration);
                    }}
                  >
                    <div className="vb-timeline-track" />
                    <div
                      className="vb-timeline-seg"
                      style={{ left: `${pct(segT)}%`, width: `${Math.min(100 - pct(segT), (SEG_S / duration) * 100)}%` }}
                    />
                    <div className="vb-timeline-progress" style={{ width: `${pct(posT)}%` }} />
                    {hitsSorted.map(h => (
                      <div
                        key={`${h.t}-${h.loc}`}
                        className={"vb-hit-marker" + (Math.abs(h.t - posT) < 3 ? " active" : "")}
                        style={{ left: `${pct(h.t)}%` }}
                        title={`@${h.loc} ${h.snippet.replace(/[«»]/g, "").slice(0, 60)}`}
                        onClick={e => { e.stopPropagation(); seekTo(h.t); }}
                      />
                    ))}
                    <div className="vb-timeline-cursor" style={{ left: `${pct(posT)}%` }} />
                  </div>
                )}
                <div className="vb-time-row">
                  <span className="vb-time-cur">{fmtTime(posT)}</span>
                  {duration > 0 && <span className="vb-time-total">/ {fmtTime(duration)}</span>}
                  <span className="vb-time-seginfo">
                    片段 {fmtTime(segT)}–{fmtTime(segT + SEG_S)}{duration > 0 && segT + SEG_S < duration - 0.5 ? "（播完自动续）" : ""}
                  </span>
                </div>
              </>
            )}
            {mode === "frames" && (
              <div className="video-preview-fallback">
                <div className="video-preview-fallback-note">
                  流式播放失败（ffmpeg 异常？），已切到帧预览<br />
                  <span>命中点前后 {FRAME_FALLBACK_SPAN_S}s 共 {FRAME_FALLBACK_COUNT} 帧</span>
                </div>
                <img
                  key={frameIdx}
                  src={`${baseUrl}/api/video/frame?path=${encodeURIComponent(path)}&t=${frameT(frameIdx).toFixed(2)}&max_w=720`}
                  alt={`frame at ${frameT(frameIdx).toFixed(1)}s`}
                  className="video-preview-frame"
                />
                <input
                  type="range"
                  min={0}
                  max={FRAME_FALLBACK_COUNT - 1}
                  value={frameIdx}
                  onChange={e => setFrameIdx(parseInt(e.target.value, 10))}
                  className="video-preview-frame-slider"
                />
                <div className="video-preview-frame-time">
                  {frameT(frameIdx).toFixed(1)}s（{frameIdx + 1}/{FRAME_FALLBACK_COUNT}）
                </div>
              </div>
            )}
            {mode === "error" && <div className="video-preview-err">视频打开失败（文件被占用或已移动？）</div>}
          </div>
          {/* 命中片段列表：全部命中点，点击跳转播放 */}
          {mode !== "error" && hitsSorted.length > 0 && (
            <div className="vb-hits">
              <div className="vb-hits-title">命中片段 · {hitsSorted.length}</div>
              {hitsSorted.map(h => (
                <button
                  key={`${h.t}-${h.loc}`}
                  className={"vb-hit" + (Math.abs(h.t - posT) < 3 ? " active" : "")}
                  onClick={() => seekTo(h.t)}
                  title={`跳到 ${h.loc} 播放`}
                >
                  <span className="vb-hit-head">
                    <span className="vb-hit-loc">@{h.loc}</span>
                    <span className={"vb-hit-src " + (h.source === "sem" ? "sem" : "kw")}>
                      {h.source === "sem" ? "语义" : "关键词"}
                    </span>
                    <span className="vb-hit-score">{h.score.toFixed(2)}</span>
                  </span>
                  {h.snippet && <span className="vb-hit-snippet">{h.snippet.replace(/[«»]/g, "")}</span>}
                </button>
              ))}
              {hitsSorted.length >= 8 && <div className="vb-hits-more">仅显示前 8 个命中点</div>}
            </div>
          )}
        </div>
        <div className="video-preview-hint">
          {mode === "video" && `已从 ${targetSec}s 命中点开始播放 · 时间轴点击跳转 · ←→ ±5s · 空格 暂停 · Esc 关闭`}
          {mode === "frames" && "← → 切帧  ·  Esc 关闭  ·  拖动滑杆扫前后画面"}
        </div>
      </div>
    </div>
  );
}
