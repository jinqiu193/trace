import { useState } from "react";
import { SearchPanel, ImageSibling } from "./components/SearchPanel";
import { RootManager } from "./components/RootManager";
import { StatusBar } from "./components/StatusBar";
import { VideoPreview } from "./components/VideoPreview";
import { ImagePreview } from "./components/ImagePreview";
import { SettingsModal } from "./components/SettingsModal";
import { ShortcutHelp } from "./components/ShortcutHelp";
import { IconSearch, IconSettings, IconKeyboard } from "./icons";
import type { VideoHit } from "./api";

export default function App() {
  const [statusTick, setStatusTick] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [videoPreview, setVideoPreview] = useState<{ path: string; location: string; hits?: VideoHit[] } | null>(null);
  const [imagePreview, setImagePreview] = useState<{
    path: string;
    siblings: ImageSibling[];
  } | null>(null);

  return (
    <div className="app">
      <header className="menubar">
        <span className="menubar-logo"><IconSearch size={12} /></span>
        <span className="menubar-title">Trace</span>
        <span className="menubar-sub">本地文件夹语义检索</span>
        <span className="menubar-spacer" />
        <button
          className="menubar-btn"
          onClick={() => setHelpOpen(true)}
          title="键盘快捷键 (?)"
        >
          <IconKeyboard size={14} />
          快捷键
        </button>
        <button
          className="menubar-btn"
          onClick={() => setSettingsOpen(true)}
          title="设置"
        >
          <IconSettings size={14} />
          设置
        </button>
      </header>
      <main className="main">
        <SearchPanel
          onPlayVideo={(path, location, hits) => setVideoPreview({ path, location, hits })}
          onPreviewImage={(path, siblings) => setImagePreview({ path, siblings })}
          onShowHelp={() => setHelpOpen(true)}
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
    </div>
  );
}