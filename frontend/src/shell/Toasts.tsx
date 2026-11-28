import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type Tone = "ok" | "bad";
interface Toast {
  id: number;
  tone: Tone;
  text: string;
}

interface Toaster {
  ok: (text: string) => void;
  bad: (text: string) => void;
}

const ToastContext = createContext<Toaster | null>(null);

/**
 * Short notes that something happened ("Invoice INV-2026-00012 posted")
 * or did not. Read out by screen readers; gone after a few seconds, or a
 * little longer for a refusal, which needs reading.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const push = useCallback((tone: Tone, text: string) => {
    const id = next.current++;
    setToasts((all) => [...all.slice(-3), { id, tone, text }]);
    window.setTimeout(() => setToasts((all) => all.filter((toast) => toast.id !== id)), tone === "ok" ? 3500 : 8000);
  }, []);
  const value = useMemo<Toaster>(() => ({ ok: (text) => push("ok", text), bad: (text) => push("bad", text) }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.tone}`}>
            {toast.text}
            <button type="button" aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((t) => t.id !== toast.id))}>×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): Toaster {
  const toaster = useContext(ToastContext);
  if (!toaster) throw new Error("useToast outside ToastProvider");
  return toaster;
}
