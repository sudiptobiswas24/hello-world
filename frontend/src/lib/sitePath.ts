/**
 * A link read from a stored row, as a path on this site, or null (O317).
 *
 * The server refuses to keep anything else (apps/core/site_paths.py,
 * same_site_path), and this asks the same before a screen renders one, so
 * a row stored before that rule is harmless too. A browser reads a
 * backslash as a slash and drops tabs and line breaks, so `/\host` and
 * `/<tab>/host` lead to `//host`, another site; percent-encoding hides the
 * same (`/%5chost`). The path is parsed against this site, and kept only if
 * it stays here.
 */
export function sitePath(link: string | null | undefined): string | null {
  if (typeof link !== "string" || !link) return null;
  let seen = link;
  const forms = [link];
  for (let i = 0; i < 3; i += 1) {
    try {
      seen = decodeURIComponent(seen);
    } catch {
      return null;
    }
    forms.push(seen);
  }
  for (const form of forms) {
    if (!form.startsWith("/") || form.startsWith("//") || form.includes("\\")) return null;
    for (let i = 0; i < form.length; i += 1) {
      const code = form.charCodeAt(i);
      if (code < 32 || code === 127) return null;
    }
  }
  const base = "https://site.invalid";
  let parsed: URL;
  try {
    parsed = new URL(link, base);
  } catch {
    return null;
  }
  if (parsed.origin !== base) return null;
  // The parser resolves dot segments as the browser will: `/.//host` and `/x/..//host` come out as `//host`,
  // which a link follows to another site (review_sechr F1). What is kept is the resolved path, and only if it
  // still starts with a single slash.
  if (parsed.pathname.startsWith("//")) return null;
  return parsed.pathname + parsed.search + parsed.hash;
}
