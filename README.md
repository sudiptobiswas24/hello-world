# hello-world
my first repository
hey guys how are you doing???

=============================


have been working on a projecct for a while..what do u sae??
3

## PP Sacks Calculator

A costing and production-planning calculator for polypropylene woven sacks,
built from the "Polypropylene Sacks Calculator App" design (v2).

Screens: costing calculator (sheet / split / cards views and a full cost sheet
per bag spec), material rates with history and bulk updates, settings,
machines & planning, a printable job card, and a printable quote.
All data is saved in the browser (IndexedDB, with localStorage as a fast-start
cache). Use **Export** / **Import** in the sidebar to back it up.

### Run it

```bash
npm ci
npm run dev
```

Other scripts: `npm test` (engine unit tests), `npm run build` (static build in `dist/`).

### Layout

- `src/engine/` — the calculation engine (pure TypeScript, no React): bag study
  chain, costing, planning, QC strength model, tape-mix analysis, persistence.
- `src/state/store.tsx` — app state, actions and debounced saving.
- `src/components/ui/` — shared UI primitives (buttons, fields, cards, icons).
- `src/components/shell/` — sidebar and top bar.
- `src/screens/` — one folder per screen.
- `src/styles/tokens.css` — design tokens (colours, radii, type sizes).
