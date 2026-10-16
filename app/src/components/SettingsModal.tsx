import { useEffect } from "react";

export function SettingsModal({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="settings-overlay"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="settings-window">
        <div className="settings-header">
          <span>设置</span>
          <button className="settings-close" onClick={onClose} title="关闭 (Esc)">✕</button>
        </div>
        <div className="settings-body">{children}</div>
      </div>
    </div>
  );
}