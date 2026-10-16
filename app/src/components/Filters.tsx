const TYPES = ["text", "pdf", "image", "code", "audio", "video"] as const;

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
  return (
    <div className="filters">
      <div className="filter-row">
        <label>类型</label>
        <div className="type-buttons">
          {TYPES.map(t => (
            <button
              key={t}
              className={"type-btn" + (props.fileType === t ? " active" : "")}
              onClick={() => props.setFileType(props.fileType === t ? "" : t)}
              title={props.facets && props.facets[t] > 0 ? `候选池中有 ${props.facets[t]} 个` : undefined}
            >
              {t}
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
          placeholder="路径子串，例如 testbed"
          spellCheck={false}
        />
      </div>
      <div className="filter-row">
        <label>相似度阈值</label>
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