import { useEffect } from "react";
import { IconClose, IconKeyboard } from "../icons";

function K({ children }: { children: React.ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

const SECTIONS: { title: string; rows: [React.ReactNode, string][] }[] = [
  {
    title: "搜索",
    rows: [
      [<><K>Ctrl</K> <K>K</K> 或 <K>/</K></>, "聚焦搜索框（任意位置）"],
      [<><K>↑</K> <K>↓</K></>, "在结果之间移动选择"],
      [<><K>Enter</K></>, "打开选中的结果"],
      [<><K>Esc</K></>, "逐层退出：关历史 → 清搜索词 → 退出图片/相似模式"],
      [<><K>Ctrl</K> <K>V</K></>, "粘贴剪贴板图片 → 以图搜图"],
      [<K>拖拽图片</K>, "拖一张图到窗口任意位置即可搜相似"],
    ],
  },
  {
    title: "视图",
    rows: [
      [<><K>Ctrl</K> <K>G</K></>, "切换 列表 / 网格 视图"],
      [<K>双击结果</K>, "在文件管理器中定位该文件"],
    ],
  },
  {
    title: "图片预览",
    rows: [
      [<K>滚轮 / 拖动</K>, "缩放 / 平移"],
      [<><K>+</K> <K>−</K> <K>0</K> <K>F</K></>, "放大 / 缩小 / 1:1 / 适应窗口"],
      [<><K>←</K> <K>→</K></>, "在同批图片之间翻页"],
    ],
  },
  {
    title: "全局",
    rows: [
      [<K>?</K>, "打开本帮助"],
      [<K>Esc</K>, "关闭弹窗"],
    ],
  },
];

export function ShortcutHelp({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="help-overlay"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="help-window">
        <div className="help-header">
          <IconKeyboard size={15} />
          <span>键盘快捷键</span>
          <button className="settings-close" style={{ marginLeft: "auto" }} onClick={onClose} title="关闭 (Esc)">
            <IconClose size={14} />
          </button>
        </div>
        <div className="help-body">
          {SECTIONS.map(sec => (
            <div key={sec.title}>
              <div className="help-section">{sec.title}</div>
              <div className="help-grid">
                {sec.rows.map(([keys, desc], i) => (
                  <div key={i} style={{ display: "contents" }}>
                    <div className="help-keys">{keys}</div>
                    <div className="help-desc">{desc}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}