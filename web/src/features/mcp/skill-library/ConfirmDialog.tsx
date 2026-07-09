import type { ReactNode } from "react";
import { RotateCcw, Trash2 } from "lucide-react";

/**
 * 通用二次确认弹窗：紫(可逆,如回滚) / 红(不可逆,如删除)。
 * 具体文案(diff chip / 警告条)由调用方通过 body 传入，本组件只负责壳与配色。
 */
export function ConfirmDialog({
  tone,
  title,
  body,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
  tone: "purple" | "red";
  title: string;
  body: ReactNode;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const iconBg = tone === "purple" ? "var(--accent-soft)" : "var(--red-soft)";
  const iconColor = tone === "purple" ? "var(--accent)" : "var(--red)";
  const Icon = tone === "purple" ? RotateCcw : Trash2;

  return (
    <div
      role="presentation"
      onClick={onCancel}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(6,7,14,0.65)",
        backdropFilter: "blur(2px)",
        display: "grid",
        placeItems: "center",
        zIndex: 3000,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(440px, 100%)",
          background: "var(--surface-2)",
          border: "1px solid var(--hair-2)",
          borderRadius: "var(--r-lg)",
          padding: 24,
          boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              background: iconBg,
              color: iconColor,
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
            }}
          >
            <Icon size={17} />
          </div>
          <h3
            style={{
              fontFamily: "var(--display)",
              fontSize: 17,
              fontWeight: 650,
              color: "var(--fg)",
              margin: 0,
            }}
          >
            {title}
          </h3>
        </div>

        <div style={{ color: "var(--fg-2)", fontSize: 13.5, lineHeight: 1.75, display: "grid", gap: 10 }}>
          {body}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 22 }}>
          <button type="button" className="secondaryButton" onClick={onCancel}>
            取消
          </button>
          {tone === "purple" ? (
            <button type="button" className="primaryButton" onClick={onConfirm}>
              <RotateCcw size={14} /> {confirmLabel}
            </button>
          ) : (
            <button
              type="button"
              onClick={onConfirm}
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 7,
                height: 38,
                padding: "0 16px",
                borderRadius: "var(--r)",
                fontSize: 13,
                fontWeight: 600,
                background: "var(--red)",
                color: "#fff",
                border: "1px solid transparent",
                boxShadow: "0 6px 20px rgba(248,113,113,0.35)",
                cursor: "pointer",
              }}
            >
              <Trash2 size={14} /> {confirmLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
