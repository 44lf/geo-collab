import type { ReactNode } from "react";

export function Modal({
  title,
  children,
  footer,
  onClose,
}: {
  title: string;
  children: ReactNode;
  footer: ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="modalBackdrop"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modalHeader">
          <h3>{title}</h3>
          <button className="iconButton" type="button" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="modalContent">{children}</div>
        <footer className="modalActions">{footer}</footer>
      </div>
    </div>
  );
}
