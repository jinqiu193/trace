import { useState } from "react";
import { SearchPanel, ImageSibling } from "./components/SearchPanel";
import { RootManager } from "./components/RootManager";
import { StatusBar } from "./components/StatusBar";
import { Filters } from "./components/Filters";
import { VideoPreview } from "./components/VideoPreview";
import { ImagePreview } from "./components/ImagePreview";
import type { VideoHit } from "./api";

export default function App() {
  const [statusTick, setStatusTick] = useState(0);
  const [fileType, setFileType] = useState("");
  const [dir, setDir] = useState("");
  const [threshold, setThreshold] = useState(0);
  const [resultCount, setResultCount] = useState(0);
  const [videoPreview, setVideoPreview] = useState<{ path: string; location: string; hits?: VideoHit[] } | null>(null);
  const [imagePreview, setImagePreview] = useState<{
    path: string;
    siblings: ImageSibling[];
  } | null>(null);

  return (
    <div className="app">
      <aside className="sidebar">
        <h1 className="app-title">Trace</h1>
        <p className="app-sub">本地文件夹语义检索</p>
        <RootManager onChanged={() => setStatusTick(t => t + 1)} />
        <Filters
          fileType={fileType}
          setFileType={setFileType}
          dir={dir}
          setDir={setDir}
          threshold={threshold}
          setThreshold={setThreshold}
          resultCount={resultCount}
        />
      </aside>
      <main className="main">
        <SearchPanel
          fileType={fileType}
          dir={dir}
          threshold={threshold}
          onPlayVideo={(path, location, hits) => setVideoPreview({ path, location, hits })}
          onPreviewImage={(path, siblings) => setImagePreview({ path, siblings })}
          onResultCount={setResultCount}
        />
      </main>
      <StatusBar tick={statusTick} />
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