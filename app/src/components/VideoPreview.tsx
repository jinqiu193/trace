import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { api } from "../api";

export interface VideoPreviewProps {
  path: string;
  location: string; // "HH:MM:SS"
  onClose: () => void;
}

function tsToSeconds(s: string): number {
  const parts = s.split(":").map(p => parseInt(p, 10) || 0);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] || 0;
}

const STREAM_DUR_S = 90;       // 一次从匹配点往后剪 90 秒
const FRAME_FALLBACK_COUNT = 12;
const FRAME_FALLBACK_SPAN_S = 12;

export function VideoPreview({ path, location, onClose }: VideoPreviewProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [mode, setMode] = useState<"video" | "frames" | "error">("video");
  const [err, setErr] = useState<string | null>(null);
  const [frameIdx, setFrameIdx] = useState(0);
  const [frameTimestamps, setFrameTimestamps] = useState<number[]>([]);

  const targetSec = tsToSeconds(location);
  const baseUrl = api.baseUrl();
  const frameT = (i: number) => frameTimestamps[i] ?? targetSec;

  // ---- 视频模式：直接用后端 ffmpeg 流（WebM/VP9，WebView2 必支持）----
  useEffect(() => {
    if (mode !== "video") return;
    const v = videoRef.current;
    if (!v) return;
    const onLoaded = () => {
      // 视频已从 t=targetSec 开始剪了，currentTime 仍设 0（流的开头就是匹配点）
      v.currentTime = 0;
      v.play().catch(() => {/* 用户可能没点 */});
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
    v.addEventListener("error", onError);
    return () => {
      v.removeEventListener("loadedmetadata", onLoaded);
      v.removeEventListener("error", onError);
    };
  }, [mode, targetSec]);

  // ---- 键盘 ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (mode === "video" && e.key === " " && videoRef.current) {
        e.preventDefault();
        if (videoRef.current.paused) videoRef.current.play();
        else videoRef.current.pause();
      }
      if (mode === "frames") {
        if (e.key === "ArrowRight") { e.preventDefault(); setFrameIdx(i => Math.min(i + 1, FRAME_FALLBACK_COUNT - 1)); }
        if (e.key === "ArrowLeft") { e.preventDefault(); setFrameIdx(i => Math.max(i - 1, 0)); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, mode]);

  // 视频 src：后端 ffmpeg 流（WebM/VP9）
  const streamUrl = `${baseUrl}/api/video/stream?path=${encodeURIComponent(path)}&t=${targetSec.toFixed(2)}&dur=${STREAM_DUR_S}`;

  return (
    <div className="video-preview-overlay" onClick={onClose}>
      <div className="video-preview-window" onClick={e => e.stopPropagation()}>
        <div className="video-preview-header">
          <span className="video-preview-loc">@{location}（{targetSec}s）</span>
          <span className="video-preview-name">{path.split(/[\\/]/).pop()}</span>
          {mode === "frames" && (
            <span className="video-preview-mode-tag">帧回退</span>
          )}
          <button className="video-preview-close" onClick={onClose} title="关闭（Esc）">✕</button>
        </div>
        <div className="video-preview-body">
          {mode === "video" && (
            <video
              ref={videoRef}
              src={streamUrl}
              controls
              autoPlay
              muted={false}
              preload="auto"
              className="video-preview-player"
            />
          )}
          {mode === "frames" && (
            <div className="video-preview-fallback">
              <div className="video-preview-fallback-note">
                流式播放失败（ffmpeg 异常？），已切到帧预览<br />
                <span>前后 {FRAME_FALLBACK_SPAN_S}s 共 {FRAME_FALLBACK_COUNT} 帧</span>
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
          {mode === "error" && err && <div className="video-preview-err">{err}</div>}
        </div>
        <div className="video-preview-hint">
          {mode === "video" && `已从 ${targetSec}s 开始播放（${STREAM_DUR_S}s 片段）·  Esc 关闭  ·  空格 暂停/播放`}
          {mode === "frames" && "← → 切帧  ·  Esc 关闭  ·  拖动滑杆扫前后画面"}
        </div>
      </div>
    </div>
  );
}
