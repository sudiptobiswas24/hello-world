import { describe, expect, it } from "vitest";

// Every screen's source, as text.
const sources = import.meta.glob(["../**/*.tsx", "../**/*.ts", "!../**/*.test.ts", "!../**/*.test.tsx"],
  { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("an action's questions", () => {
  it("are asked in a labelled dialog, never window.prompt (O418)", () => {
    // A prompt's box has no label: read blank by a screen reader, and no test can find it by name.
    const prompting = Object.entries(sources)
      .filter(([, text]) => /\bwindow\.prompt\s*\(|(^|[^.\w])prompt\s*\(/m.test(text.replace(/\/\/.*$|\/\*[\s\S]*?\*\//gm, "")))
      .map(([path]) => path);
    expect(Object.keys(sources).length).toBeGreaterThan(100);
    expect(prompting).toEqual([]);
  });
});
