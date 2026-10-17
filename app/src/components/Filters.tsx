const TYPES = ["text", "pdf", "image", "code", "audio", "video"] as const;

const TYPE_COLORS: Record<string, string> = {
  text: "#67e8f9",
  pdf: "#fb923c",
  image: "#c084fc",
  code: "#a3e635",
  audio: "#fbbf24",
  video: "#fb7185",
};

const TYPE_LABELS: Record<string, string> = {
  text: "文本", pdf: "PDF", image: "图片", code: "代码", audio: "音频", video: "视频",
};

export function Filters(props: {
  fileType: string;
  setFileType: (t: string) => void;
  dir: string;
  setDir: (d: string) => void;
  threshold: number;
  setThreshold: (n: number) => void;
  resultCount: number;
  facets?: Record<string, number>;
}) {
  const dirty = props.fileType !== "" || props.dir !== "" || props.threshold > 0;
  const reset = () => {
    props.setFileType("");
    props.setDir("");
    props.setThreshold(0);
  };

  return (
    <div className="filters">
      <div className="filters-head">
        <span className="filters-title">筛选</span>
        {dirty && (
          <button className="filters-reset" onClick={reset} title="清除全部筛选条件">
            ✕ 重置
          </button>
        )}
      </div>
      <div className="filter-row">
        <label>类型</label>
        <div className="type-buttons">
          {TYPES.map(t => (
            <button
              key={t}
              className={"type-btn" + (props.fileType === t ? " active" : "")}
              onClick={() => props.setFileType(props.fileType === t ? "" : t)}
              title={props.facets && props.facets[t] > 0 ? `候选池中有 ${props.facets[t]} 个` : "候选池为空"}
            >
              <span className="type-dot" style={{ background: TYPE_COLORS[t] }} />
              {TYPE_LABELS[t]}
              {props.facets && props.facets[t] > 0 && (
                <span className="type-count">{props.facets[t]}</span>
              )}
            </button>
          ))}
        </div>
      </div>
      <div className="filter-row">
        <label>目录包含</label>
        <input
          value={props.dir}
          onChange={(e) => props.setDir(e.target.value)}
          placeholder="路径子串"
          spellCheck={false}
        />
      </div>
      <div className="filter-row">
        <label>相似度</label>
        <input
          type="range"
          min={0}
          max={10}
          step={0.1}
          value={props.threshold}
          onChange={(e) => props.setThreshold(parseFloat(e.target.value))}
        />
        <span className="threshold-val">{props.threshold.toFixed(1)}</span>
      </div>
      <div className="filter-row">
        <label>结果数</label>
        <span className="result-count">{props.resultCount}</span>
      </div>
    </div>
  );
}