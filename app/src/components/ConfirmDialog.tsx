import { useEffect } from "react";

/** 应用内确认对话框。
 *
 * 不用 window.confirm / window.prompt：WebView2 默认不接管 JS 对话框
 * （prompt 返回 null、confirm 返回 false），wry / Tauri 都没注册
 * DialogRequested 处理器 → 原生对话框在桌面包里形同虚设。
 */
export function ConfirmDialog({
  title,
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onCancel(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="overlay confirm-overlay" onClick={onCancel}>
      <div className="confirm-card" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-title">{title}</div>
        <div className="confirm-message">{message}</div>
        <div className="confirm-actions">
          <button className="btn-xs" onClick={onCancel}>{cancelText}</button>
          <button className={"btn-xs" + (danger ? " danger" : " primary")} onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
