import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, ReactNode } from 'react';
import {
  FOLD_ALLOWANCE,
  TYPE_CONFIG,
  UNIT_FACTOR,
  calc,
  defaultState,
  isBagType,
  makeSku,
  num,
  todayISO,
  withRateLogged,
} from '../engine';
import type { AppState, BagType, CalcResult, Machine, MixRow, NumInput, Sku, Unit } from '../engine';
import { loadFromIdb, loadFromLocalStorage, saveState } from '../engine/persist';

type Patch = Partial<AppState> | ((s: AppState) => Partial<AppState>);
type FieldEvent = ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>;
export type MixKey = 'tapeMix' | 'lamMix';

const SAVE_DEBOUNCE_MS = 400;
const MAX_IMAGES_PER_BAG = 6;
const IMAGE_MAX_PX = 900;

/** Reads an input's value; number inputs never store negatives. */
export function eventValue(e: FieldEvent): string {
  const t = e.target;
  let v = t.value;
  if (t.type === 'number' && v !== '' && parseFloat(v) < 0) v = String(Math.abs(parseFloat(v)));
  return v;
}

function nextJobIdOf(skus: Sku[]): string {
  let maxJob = 100;
  skus.forEach((k) => {
    const m = /JOB-(\d+)/.exec(k.jobId || '');
    if (m) maxJob = Math.max(maxJob, parseInt(m[1], 10));
  });
  return 'JOB-' + (maxJob + 1);
}

const mapSku = (s: AppState, id: string, fn: (k: Sku) => Sku): Partial<AppState> => ({
  skus: s.skus.map((k) => (k.id === id ? fn(k) : k)),
});

function createActions(setState: (p: Patch) => void, replaceState: (s: AppState) => void, markImagesDirty: () => void) {
  const updateSku = (id: string, patch: Partial<Sku> | ((k: Sku) => Partial<Sku>)) =>
    setState((s) => mapSku(s, id, (k) => Object.assign({}, k, typeof patch === 'function' ? patch(k) : patch)));

  const actions = {
    setState,
    replaceState,
    updateSku,

    /** onChange handler that writes the input's value to a spec field. */
    bindSku: (id: string, field: keyof Sku) => (e: FieldEvent) => updateSku(id, { [field]: eventValue(e) } as Partial<Sku>),

    /** onChange handler for a job-card free-text field. */
    bindJob: (id: string, key: string) => (e: FieldEvent) => {
      const v = e.target.value;
      updateSku(id, (k) => ({ job: Object.assign({}, k.job, { [key]: v }) }));
    },

    /** Change bag type and apply that type's lamination/BOPP/print defaults. */
    setSkuType: (id: string, t: string) => {
      if (!isBagType(t)) return;
      const c = TYPE_CONFIG[t as BagType];
      updateSku(id, (k) => ({
        type: t as BagType,
        lamSide: c.lamSide,
        boppVariant: c.bopp,
        printed: c.printed,
        metallic: c.aMet ? k.metallic : 'none',
        boxStyle: t === 'box' ? k.boxStyle || 'dcut' : k.boxStyle,
      }));
    },

    /** Set the bottom fold; its allowance (added to cut length) follows the fold type. */
    setFoldType: (id: string, ft: Sku['foldType']) => updateSku(id, (k) => ({ foldType: ft, fold: FOLD_ALLOWANCE[ft] ?? k.fold })),

    /** Change unit and convert width/length/gusset so the bag stays the same size. */
    setUnit: (id: string, newUnit: Unit) =>
      updateSku(id, (k) => {
        const c = (v: NumInput) => Math.round((num(v) / UNIT_FACTOR[k.unit]) * UNIT_FACTOR[newUnit] * 100) / 100;
        return { unit: newUnit, width: c(k.width), length: c(k.length), gusset: c(k.gusset) };
      }),

    select: (id: string | null) => setState({ selId: id }),

    addSku: (): string => {
      const id = 's' + Date.now();
      setState((s) => {
        const count = s.skus.filter((k) => /^New bag spec/.test(k.name)).length;
        const name = count === 0 ? 'New bag spec' : 'New bag spec ' + (count + 1);
        const n = makeSku({
          ...defaultState().skus[0],
          id, name, jobId: nextJobIdOf(s.skus), party: '', date: todayISO(),
          width: '', length: '', gusset: '', targetWt: '', warpD: '', weftD: '',
          warpMesh: '', weftMesh: '', qty: '', bagsPerBale: '', strength: '', elong: '',
          linerOn: false, handleOn: false, job: {},
        });
        return { skus: s.skus.concat([n]), selId: id };
      });
      return id;
    },

    dupSku: (id: string) => {
      const nid = 's' + Date.now();
      setState((s) => {
        const src = s.skus.find((k) => k.id === id);
        if (!src) return {};
        if (src.images && src.images.length) markImagesDirty();
        const copy: Sku = Object.assign({}, src, {
          id: nid, jobId: nextJobIdOf(s.skus), name: src.name + ' copy',
          images: (src.images || []).slice(), job: Object.assign({}, src.job),
        });
        return { skus: s.skus.concat([copy]), selId: nid };
      });
    },

    delSku: (id: string) =>
      setState((s) => {
        const rest = s.skus.filter((k) => k.id !== id);
        return { skus: rest, selId: s.selId === id ? (rest[0] ? rest[0].id : null) : s.selId };
      }),

    updMixRow: (skuId: string, key: MixKey, idx: number, patch: Partial<MixRow>) =>
      updateSku(skuId, (k) => ({ [key]: (k[key] || []).map((r, i) => (i === idx ? Object.assign({}, r, patch) : r)) })),

    /** Append a blend row using the section's first material not already in the mix. */
    addMixRow: (skuId: string, key: MixKey, secId: string) =>
      setState((s) => {
        const sec = s.matSections.find((x) => x.id === secId);
        return mapSku(s, skuId, (k) => {
          const used = (k[key] || []).map((r) => r.m);
          const next = sec ? sec.items.find((it) => !used.includes(it.id)) || sec.items[0] : null;
          return Object.assign({}, k, { [key]: (k[key] || []).concat([{ m: next ? next.id : '', wt: '' }]) });
        });
      }),

    delMixRow: (skuId: string, key: MixKey, idx: number) =>
      updateSku(skuId, (k) => ({ [key]: (k[key] || []).filter((_, i) => i !== idx) })),

    setMix: (skuId: string, key: MixKey, mix: MixRow[]) => updateSku(skuId, { [key]: mix } as Partial<Sku>),

    // ----- machines -----
    updMachine: (id: string, patch: Partial<Machine>) =>
      setState((s) => ({ machines: s.machines.map((m) => (m.id === id ? Object.assign({}, m, patch) : m)) })),
    updMachineP: (id: string, key: string, val: NumInput) =>
      setState((s) => ({ machines: s.machines.map((m) => (m.id === id ? Object.assign({}, m, { p: Object.assign({}, m.p, { [key]: val }) }) : m)) })),
    addMachine: () =>
      setState((s) => ({ machines: s.machines.concat([{ id: 'm' + Date.now(), name: 'New machine', typ: 'loom', p: { rpm: 90, shuttles: 6 }, count: 1, eff: 85 }]) })),
    delMachine: (id: string) => setState((s) => ({ machines: s.machines.filter((m) => m.id !== id) })),

    // ----- material rates + history -----
    /** Edit a material; a rate change is also logged to history as effective today. */
    setMatItem: (secId: string, itemId: string, patch: { name?: string; rate?: NumInput }) =>
      setState((s) => {
        const matSections = s.matSections.map((sec) =>
          sec.id !== secId ? sec : Object.assign({}, sec, { items: sec.items.map((it) => (it.id === itemId ? Object.assign({}, it, patch) : it)) }),
        );
        if (patch.rate == null || num(patch.rate) <= 0) return { matSections };
        const today = todayISO();
        return { matSections, rateHistory: withRateLogged(s.rateHistory, itemId, num(patch.rate), today, today) };
      }),

    /** Commit a single drafted rate (Rates page "Save" per row). */
    saveMatRate: (secId: string, itemId: string) =>
      setState((s) => {
        const v = num((s.rateDraft || {})[itemId]);
        if (!(v > 0)) return {};
        const today = todayISO();
        const matSections = s.matSections.map((sec) =>
          sec.id !== secId ? sec : Object.assign({}, sec, { items: sec.items.map((it) => (it.id === itemId ? Object.assign({}, it, { rate: v }) : it)) }),
        );
        const rateDraft = Object.assign({}, s.rateDraft);
        delete rateDraft[itemId];
        return { matSections, rateHistory: withRateLogged(s.rateHistory, itemId, v, today, today), rateDraft };
      }),

    /** Commit every drafted rate at the chosen effective date; list rates change only if effective now. */
    bulkSave: () => {
      let saved = 0;
      setState((s) => {
        const today = todayISO();
        const eff = s.bulkEff || today;
        const draft = s.bulkDraft || {};
        const ids = Object.keys(draft).filter((id) => num(draft[id]) > 0);
        if (!ids.length) return {};
        saved = ids.length;
        let hist = s.rateHistory || [];
        ids.forEach((id) => {
          hist = withRateLogged(hist, id, num(draft[id]), eff, today);
        });
        let sections = s.matSections;
        if (eff <= today) {
          sections = sections.map((sec) =>
            Object.assign({}, sec, { items: sec.items.map((it) => (ids.indexOf(it.id) >= 0 ? Object.assign({}, it, { rate: num(draft[it.id]) }) : it)) }),
          );
        }
        return { rateHistory: hist, matSections: sections, bulkDraft: {}, bulkFlash: ids.length };
      });
      setTimeout(() => {
        if (saved) setState({ bulkFlash: 0 });
      }, 2200);
    },

    addMatItem: (secId: string) => {
      const id = 'x' + Date.now();
      setState((s) => ({
        matSections: s.matSections.map((sec) => (sec.id !== secId ? sec : Object.assign({}, sec, { items: sec.items.concat([{ id, name: 'New material', rate: 0 }]) }))),
      }));
    },

    delMatItem: (secId: string, itemId: string) =>
      setState((s) => ({
        matSections: s.matSections.map((sec) => (sec.id !== secId ? sec : Object.assign({}, sec, { items: sec.items.filter((it) => it.id !== itemId) }))),
      })),

    /** Pick image files, downscale to ≤900 px JPEG, attach (max 6 per bag). */
    attachImages: (skuId: string) => {
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = 'image/*';
      inp.multiple = true;
      inp.onchange = () => {
        Array.from(inp.files || []).forEach((f) => {
          const rd = new FileReader();
          rd.onload = () => {
            const img = new Image();
            img.onload = () => {
              const sc = Math.min(1, IMAGE_MAX_PX / Math.max(img.width, img.height));
              const cv = document.createElement('canvas');
              cv.width = Math.round(img.width * sc);
              cv.height = Math.round(img.height * sc);
              cv.getContext('2d')?.drawImage(img, 0, 0, cv.width, cv.height);
              const url = cv.toDataURL('image/jpeg', 0.8);
              markImagesDirty();
              updateSku(skuId, (k) => {
                const cur = k.images || [];
                return cur.length >= MAX_IMAGES_PER_BAG ? {} : { images: cur.concat([url]) };
              });
            };
            img.src = rd.result as string;
          };
          rd.readAsDataURL(f);
        });
      };
      inp.click();
    },

    removeImage: (skuId: string, idx: number) => {
      markImagesDirty();
      updateSku(skuId, (k) => ({ images: (k.images || []).filter((_, i) => i !== idx) }));
    },
  };
  return actions;
}

export type Actions = ReturnType<typeof createActions>;

export interface StorageStatus {
  /** browser storage quota in bytes (0 when unknown) */
  quota: number;
  /** last save to IndexedDB failed */
  error: boolean;
}

interface StoreValue {
  state: AppState;
  actions: Actions;
  storage: StorageStatus;
}

const StoreContext = createContext<StoreValue | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, setRaw] = useState<AppState>(loadFromLocalStorage);
  const [storage, setStorage] = useState<StorageStatus>({ quota: 0, error: false });
  const dirty = useRef(false);
  const imagesDirty = useRef(false);

  const setState = useCallback((patch: Patch) => {
    dirty.current = true;
    setRaw((prev) => Object.assign({}, prev, typeof patch === 'function' ? patch(prev) : patch));
  }, []);
  const replaceState = useCallback((next: AppState) => {
    dirty.current = true;
    imagesDirty.current = true;
    setRaw(next);
  }, []);
  const actions = useMemo(() => createActions(setState, replaceState, () => (imagesDirty.current = true)), [setState, replaceState]);

  // IndexedDB holds the authoritative copy (localStorage is a fast-start cache)
  useEffect(() => {
    let live = true;
    loadFromIdb().then((res) => {
      if (!live || !res) return;
      if (res.hadInlineImages) imagesDirty.current = true;
      setRaw(res.state);
    });
    navigator.storage?.estimate?.().then((est) => live && setStorage((s) => ({ ...s, quota: est.quota || 0 })));
    return () => {
      live = false;
    };
  }, []);

  // debounced save after any change
  useEffect(() => {
    if (!dirty.current) return;
    const t = setTimeout(() => {
      dirty.current = false;
      const imgs = imagesDirty.current;
      imagesDirty.current = false;
      saveState(state, imgs).then((ok) => setStorage((s) => (s.error === !ok ? s : { ...s, error: !ok })));
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [state]);

  // a saved spec may carry a bag type that no longer exists
  useEffect(() => {
    if (state.skus.some((s) => !isBagType(s.type))) {
      setState((s) => ({ skus: s.skus.map((k) => (isBagType(k.type) ? k : Object.assign({}, k, { type: 'plain' as BagType }))) }));
    }
  }, [state.skus, setState]);

  const value = useMemo(() => ({ state, actions, storage }), [state, actions, storage]);
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreValue {
  const v = useContext(StoreContext);
  if (!v) throw new Error('useStore must be used inside <StoreProvider>');
  return v;
}

/** Calculation results for every spec, recomputed when inputs or rates change. */
export function useResults(): Record<string, CalcResult> {
  const { state } = useStore();
  const { skus, glob, wast, convs, matSections, rateHistory } = state;
  return useMemo(() => {
    const ctx = { skus, glob, wast, convs, matSections, rateHistory };
    const out: Record<string, CalcResult> = {};
    skus.forEach((s) => {
      out[s.id] = calc(ctx, s);
    });
    return out;
  }, [skus, glob, wast, convs, matSections, rateHistory]);
}
