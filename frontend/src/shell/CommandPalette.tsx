import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";

export interface Command {
  label: string;
  hint: string;
  href: string;
  keywords?: string;
}

const words = (text: string | undefined) => (text ?? "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

/**
 * How well a command answers what was typed, or 0 when it does not: every
 * word typed must start a word of the label, hint or keywords. A match on
 * the screen's own name outranks one on its module or its keywords, and
 * the name starting with it outranks a later word: "cust" is Customers,
 * not Invoices, whose keywords mention customers.
 */
export function score(command: Command, typed: string): number {
  const parts = words(typed);
  if (parts.length === 0) return 1;
  const label = words(command.label);
  const hint = words(command.hint);
  const keywords = words(command.keywords);
  let total = 0;
  for (const part of parts) {
    if (label[0]?.startsWith(part)) total += 8;
    else if (label.some((word) => word.startsWith(part))) total += 6;
    else if (hint.some((word) => word.startsWith(part))) total += 3;
    else if (keywords.some((word) => word.startsWith(part))) total += 1;
    else return 0;
  }
  return total;
}

export function matches(command: Command, typed: string): boolean {
  return score(command, typed) > 0;
}

/** The commands that answer, best first; equals keep their order. */
export function rank(commands: Command[], typed: string): Command[] {
  return commands
    .map((command, index) => ({ command, index, score: score(command, typed) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((entry) => entry.command);
}

/**
 * Ctrl+K (or ⌘K) from anywhere: type a few letters of a screen and press
 * Enter. Faster than the mouse for people who live in this all day.
 */
export function CommandPalette({ commands, open, onClose }: { commands: Command[]; open: boolean; onClose: () => void }) {
  const [typed, setTyped] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const found = useMemo(() => rank(commands, typed).slice(0, 12), [commands, typed]);

  useEffect(() => setActive(0), [typed]);

  if (!open) return null;
  // Emptied on the way out, not on the way in: the box takes focus as it
  // appears, and whatever is typed straight after Ctrl+K must land in it.
  const close = () => {
    setTyped("");
    onClose();
  };
  const go = (command: Command | undefined) => {
    if (!command) return;
    close();
    navigate(command.href);
  };
  return (
    <div className="palette-backdrop" onMouseDown={close}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Go to" onMouseDown={(e) => e.stopPropagation()}>
        <input
          autoFocus
          value={typed}
          placeholder="Go to a screen…"
          aria-label="Go to a screen"
          aria-controls="palette-results"
          aria-activedescendant={found[active] ? `palette-${active}` : undefined}
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") close();
            else if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive((i) => Math.min(i + 1, found.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (event.key === "Enter") go(found[active]);
          }}
        />
        <ul id="palette-results" role="listbox">
          {found.map((command, index) => (
            <li
              key={command.href}
              id={`palette-${index}`}
              role="option"
              aria-selected={index === active}
              className={index === active ? "active" : undefined}
              onMouseEnter={() => setActive(index)}
              onClick={() => go(command)}
            >
              <span>{command.label}</span>
              <small>{command.hint}</small>
            </li>
          ))}
          {found.length === 0 && <li className="none">Nothing called that.</li>}
        </ul>
      </div>
    </div>
  );
}
