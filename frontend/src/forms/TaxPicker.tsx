import { useEffect, useRef, useState } from "react";

interface Tax {
  id: number;
  code: string;
  name?: string;
}

/**
 * A line's taxes: what it bears, in words, opening a list to tick.
 *
 * It was a native multi-select thirty pixels tall: one option showed at a
 * time and a second needed Ctrl-click, so nobody chose any, and a line typed
 * on screen went out with no GST (O395). The line now takes its item's taxes
 * when it is added; this is where they are seen and changed. A line bearing
 * none says so, in the warning colour.
 */
export function TaxPicker({ chosen, offered, label, onChange }: {
  chosen: number[];
  offered: Tax[] | undefined;
  label: string;
  onChange: (next: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<number[]>(chosen);

  useEffect(() => { if (!open) setDraft(chosen); }, [chosen, open]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !box.current?.contains(event.target as Node)) {
        setOpen(false);
        // Kept as ticked when the list closes; one change, not one per box.
        if (JSON.stringify([...draft].sort()) !== JSON.stringify([...chosen].sort())) onChange(draft);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open, draft, chosen, onChange]);

  const codes = (ids: number[]) => ids.map((id) => offered?.find((tax) => tax.id === id)?.code ?? (offered ? `#${id}` : "…"));
  const words = chosen.length ? codes(chosen).join(", ") : "No tax";

  return (
    <div className="tax-picker" ref={box}>
      <button type="button" className={chosen.length ? "tax-chosen" : "tax-chosen none"} aria-haspopup="true"
              aria-expanded={open} aria-label={`${label}: ${words}`} onClick={() => setOpen((was) => !was)}>
        {words} ▾
      </button>
      {open && (
        <fieldset className="tax-list" aria-label={label}>
          {(offered ?? []).map((tax) => (
            <label key={tax.id}>
              <input type="checkbox" checked={draft.includes(tax.id)}
                onChange={(event) => setDraft((was) => event.target.checked ? [...was, tax.id] : was.filter((id) => id !== tax.id))} />
              {tax.code}{tax.name ? <span className="muted"> · {tax.name}</span> : null}
            </label>
          ))}
          {offered && offered.length === 0 && <span className="muted">No taxes to choose.</span>}
        </fieldset>
      )}
    </div>
  );
}
