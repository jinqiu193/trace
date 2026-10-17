import { useState } from "react";
import { SearchPanel, ImageSibling } from "./components/SearchPanel";
import { RootManager } from "./components/RootManager";
import { StatusBar } from "./components/StatusBar";
import { VideoPreview } from "./components/VideoPreview";
import { ImagePreview } from "./components/ImagePreview";
import { FilePreview } from "./components/FilePreview";
import { SettingsModal } from "./components/SettingsModal";
import { ShortcutHelp } from "./components/ShortcutHelp";
import { IconSettings, IconKeyboard, IconList, IconGrid } from "./icons";
import type { VideoHit, SearchMode, SortKey } from "./api";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "relevance", label: "相关度" },
  { key: "mtime_desc", label: "时间 新→旧" },
  { key: "mtime_asc", label: "时间 旧→新" },
  { key: "size_desc", label: "大小 大→小" },
  { key: "name", label: "文件名 A→Z" },
];

export default function App() {
  const [statusTick, setStatusTick] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [videoPreview, setVideoPreview] = useState<{ path: string; location: string; hits?: VideoHit[] } | null>(null);
  const [imagePreview, setImagePreview] = useState<{
    path: string;
    siblings: ImageSibling[];
  } | null>(null);
  const [filePreview, setFilePreview] = useState<{
    path: string;
    file_type: string;
    location?: string;
    query?: string;
  } | null>(null);

  // 检索模式 / 排序 / 视图（控件在标题栏，状态提升到此）
  const [mode, setMode] = useState<SearchMode>(() =>
    (localStorage.getItem("trace.mode") as SearchMode) === "fast" ? "fast" : "detailed");
  const switchMode = (m: SearchMode) => {
    setMode(m);
    localStorage.setItem("trace.mode", m);
  };
  const [sort, setSort] = useState<SortKey>("relevance");
  const [view, setView] = useState<"list" | "grid">("list");

  return (
    <div className="app">
      <header className="menubar">

        <span className="menubar-spacer" />
        <div className="mode-seg" role="group" aria-label="检索模式">
          <button
            className={"mode-seg-btn" + (mode === "fast" ? " active" : "")}
            onClick={() => switchMode("fast")}
            title="简单：视频每个文件仅比对 2 个代表片段，跳过命中详情组装，速度更快"
          >⚡ 简单</button>
          <button
            className={"mode-seg-btn" + (mode === "detailed" ? " active" : "")}
            onClick={() => switchMode("detailed")}
            title="详细：全切片比对 + 时间轴命中标记 + 周边帧，结果最全（默认）"
          >详细</button>
        </div>
        <select className="sort-select" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} title="结果排序">
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
        <span className="menubar-sep" />
        <button
          className="menubar-btn"
          onClick={() => setHelpOpen(true)}
          title="键盘快捷键 (?)"
        >
          <IconKeyboard size={14} />
        </button>
        <button
          className="menubar-btn"
          onClick={() => setSettingsOpen(true)}
          title="设置"
        >
          <IconSettings size={14} />
        </button>
      </header>
      <main className="main">
        <SearchPanel
          onPlayVideo={(path, location, hits) => setVideoPreview({ path, location, hits })}
          onPreviewImage={(path, siblings) => setImagePreview({ path, siblings })}
          onPreviewFile={(path, file_type, location, query) => setFilePreview({ path, file_type, location, query })}
          onShowHelp={() => setHelpOpen(true)}
          rootsTick={statusTick}
          mode={mode}
          sort={sort}
          view={view}
          onViewChange={setView}
        />
      </main>
      <StatusBar tick={statusTick} />
      {helpOpen && <ShortcutHelp onClose={() => setHelpOpen(false)} />}
      {settingsOpen && (
        <SettingsModal onClose={() => setSettingsOpen(false)}>
          <RootManager onChanged={() => setStatusTick(t => t + 1)} />
          <div className="settings-hint">
            添加文件夹后会在后台自动建立索引；已添加的文件夹由监听器自动同步文件变更（若已启用）。
            索引进度显示在底部状态栏，可随时关闭本窗口，不影响后台任务。
          </div>
        </SettingsModal>
      )}
      {videoPreview && (
        <VideoPreview
          path={videoPreview.path}
          location={videoPreview.location}
          hits={videoPreview.hits}
          onClose={() => setVideoPreview(null)}
        />
      )}
      {imagePreview && (
        <ImagePreview
          path={imagePreview.path}
          siblings={imagePreview.siblings}
          onClose={() => setImagePreview(null)}
          onNavigate={(p) => setImagePreview(prev => prev ? { ...prev, path: p } : prev)}
        />
      )}
      {filePreview && (
        <FilePreview
          path={filePreview.path}
          file_type={filePreview.file_type}
          location={filePreview.location}
          query={filePreview.query}
          onClose={() => setFilePreview(null)}
        />
      )}
    </div>
  );
}