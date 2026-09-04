import { useEffect, useRef } from "react";
import { X } from "lucide-react";

// One overlay surface for the whole app: a bottom sheet on phones, a right-hand side panel from md up.
// Detail lives here instead of expanding inline, so the cards behind it stay dense and calm.
export default function Drawer({ open, onClose, title, subtitle, children, footer }) {
  const panelRef = useRef(null);
  const returnFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement;
    panelRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    // Lock the page behind the sheet so a phone doesn't scroll two surfaces at once.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      const el = returnFocusRef.current;
      if (el && typeof el.focus === "function") el.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end md:items-stretch md:justify-end">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px] animate-fade-in" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="relative w-full md:w-[26rem] lg:w-[28rem] max-h-[88vh] md:max-h-none md:h-full
                   bg-surface rounded-t-2xl md:rounded-none shadow-drawer flex flex-col
                   animate-slide-up md:animate-slide-left focus:outline-none"
      >
        <div className="md:hidden pt-2.5 pb-1 flex justify-center shrink-0">
          <span className="w-9 h-1 rounded-full bg-line-strong" />
        </div>
        <header className="flex items-start justify-between gap-3 px-5 pt-3 pb-4 md:pt-5 border-b border-line shrink-0">
          <div className="min-w-0">
            <h2 className="font-semibold text-ink truncate">{title}</h2>
            {subtitle && <p className="text-xs text-ink-3 mt-0.5">{subtitle}</p>}
          </div>
          <button onClick={onClose} aria-label="Close" className="btn btn-ghost p-1.5 -mr-1 shrink-0">
            <X size={18} />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4 grow">{children}</div>
        {footer && <div className="border-t border-line px-5 py-3 shrink-0">{footer}</div>}
      </div>
    </div>
  );
}
