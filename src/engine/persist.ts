import { FOLD_ALLOWANCE, defaultState, isFoldType } from './defaults';
import type { AppState, BagType, Sku } from './types';

/** Storage keys shared with the original design, so saved work carries over. */
export const LS_KEY = 'pp-sacks-calc-v2';
const IDB_NAME = 'pp-sacks-calc';
const IDB_STORE = 'kv';
/** localStorage is only a fast-start cache; skip it for very large states. */
const LS_MAX_CHARS = 3 * 1048576;

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** Bag types that older versions had, mapped onto the current set. */
const LEGACY_TYPES: Record<string, BagType> = { liner: 'plain', handle: 'box', tea: 'bopp', boxDcut: 'box' };

/**
 * Parse a saved JSON state and bring it up to the current shape:
 * fill new fields from defaults, map legacy bag types, and merge newly
 * added default materials into saved rate sections.
 */
export function migrateState(raw: string | null | undefined): AppState {
  const d = defaultState();
  if (!raw) return d;
  let s: Partial<AppState> & Record<string, unknown>;
  try {
    s = JSON.parse(raw);
  } catch {
    return d;
  }
  if (!s || typeof s !== 'object') return d;
  const base = d.skus[0];
  const skus = ((s.skus as Sku[] | undefined) || d.skus).map((k) => {
    const merged: Sku = Object.assign({}, base, k);
    merged.tapeMix = k.tapeMix && k.tapeMix.length ? k.tapeMix : clone(base.tapeMix);
    merged.lamMix = k.lamMix && k.lamMix.length ? k.lamMix : clone(base.lamMix);
    merged.inkSel = k.inkSel && k.inkSel.length ? k.inkSel : base.inkSel.slice();
    merged.inkCodes = k.inkCodes || {};
    const legacy = LEGACY_TYPES[k.type as string];
    if (legacy) {
      merged.type = legacy;
      if ((k.type as string) === 'liner') merged.linerOn = true;
      if ((k.type as string) === 'handle') {
        merged.boxStyle = 'handleLoop';
        merged.handleOn = true;
      }
      if ((k.type as string) === 'boxDcut') merged.boxStyle = 'dcut';
    }
    if (k.gussetOn != null && k.gussetSide == null) merged.gussetSide = k.gussetOn ? 'single' : 'none';
    if ((merged.handleG as number) > 0 && !merged.handleOn) merged.handleOn = true;
    if (!isFoldType(merged.foldType)) {
      merged.foldType = 'SFDS';
      merged.fold = 1.25;
    } else {
      merged.fold = FOLD_ALLOWANCE[merged.foldType];
    }
    if (!merged.fabricType) merged.fabricType = k.look === 'natural' ? 'natural' : 'milky';
    if (!merged.handleWt) merged.handleWt = 2;
    return merged;
  });

  const out: AppState = Object.assign(d, s, {
    skus,
    mats: Object.assign(d.mats, s.mats),
    convs: Object.assign(d.convs, s.convs),
    wast: Object.assign(d.wast, s.wast),
    glob: Object.assign(d.glob, s.glob),
    quote: Object.assign(d.quote, s.quote),
  }) as AppState;
  if (!Array.isArray(out.qcLog)) out.qcLog = [];
  if (!Array.isArray(out.matSections)) out.matSections = [];

  // merge newly added default materials into saved sections
  defaultState().matSections.forEach((ds) => {
    const sec = out.matSections.find((x) => x.id === ds.id);
    if (!sec) out.matSections.push(ds);
    else ds.items.forEach((it) => {
      if (!sec.items.find((x) => x.id === it.id)) sec.items.push(Object.assign({}, it));
    });
  });

  // migrate old flat rates into sections
  if (!s.matSections) {
    const set = (secId: string, id: string, r: number) => {
      const sc = out.matSections.find((x) => x.id === secId);
      const it = sc && sc.items.find((x) => x.id === id);
      if (it && r > 0) it.rate = r;
    };
    const om = (s.mats || {}) as Record<string, number>;
    set('tape', 'pp', +om.pp);
    set('tape', 'cc', +om.filler);
    set('tape', 'colmb', +om.mb);
    set('lam', 'lampp', +om.lamCoat);
    set('boppSec', 'boppFilm', +om.bopp);
    set('finish', 'liner', +om.liner);
    set('finish', 'thread', +om.thread);
  }
  return out;
}

export function loadFromLocalStorage(): AppState {
  try {
    return migrateState(localStorage.getItem(LS_KEY));
  } catch {
    return defaultState();
  }
}

// ===== IndexedDB (holds large states and attached images) =====

let dbPromise: Promise<IDBDatabase> | null = null;

function idb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const rq = indexedDB.open(IDB_NAME, 1);
    rq.onupgradeneeded = () => rq.result.createObjectStore(IDB_STORE);
    rq.onsuccess = () => resolve(rq.result);
    rq.onerror = () => reject(rq.error);
  });
  return dbPromise;
}

export function idbPut(key: string, txt: string): Promise<void> {
  return idb().then(
    (db) =>
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(IDB_STORE, 'readwrite');
        tx.objectStore(IDB_STORE).put(txt, key);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      }),
  );
}

export function idbGet(key: string): Promise<string | null> {
  return idb()
    .then(
      (db) =>
        new Promise<string | null>((resolve, reject) => {
          const rq = db.transaction(IDB_STORE).objectStore(IDB_STORE).get(key);
          rq.onsuccess = () => resolve((rq.result as string) ?? null);
          rq.onerror = () => reject(rq.error);
        }),
    )
    .catch(() => null);
}

/** Load the authoritative state from IndexedDB, re-attaching images. Null if nothing saved. */
export async function loadFromIdb(): Promise<{ state: AppState; hadInlineImages: boolean } | null> {
  const txt = await idbGet('state');
  if (!txt) return null;
  const state = migrateState(txt);
  const hadInlineImages = state.skus.some((s) => s.images && s.images.length);
  const imgTxt = await idbGet('images');
  if (imgTxt) {
    try {
      const im = JSON.parse(imgTxt) as Record<string, string[]>;
      state.skus = state.skus.map((s) => (im[s.id] ? Object.assign({}, s, { images: im[s.id] }) : s));
    } catch {
      /* keep state without images */
    }
  }
  return { state, hadInlineImages };
}

/**
 * Persist the state: images go to their own IndexedDB record (only when
 * changed), the lean state to IndexedDB and — when small — localStorage.
 * Resolves to false if IndexedDB rejected the write.
 */
export async function saveState(state: AppState, imagesDirty: boolean): Promise<boolean> {
  const imgMap: Record<string, string[]> = {};
  const lean: AppState = Object.assign({}, state, {
    skus: state.skus.map((s) => {
      if (s.images && s.images.length) {
        imgMap[s.id] = s.images;
        return Object.assign({}, s, { images: [] });
      }
      return s;
    }),
  });
  const txt = JSON.stringify(lean);
  try {
    if (txt.length < LS_MAX_CHARS) localStorage.setItem(LS_KEY, txt);
  } catch {
    /* quota — IndexedDB still has it */
  }
  if (imagesDirty) idbPut('images', JSON.stringify(imgMap)).catch(() => {});
  try {
    await idbPut('state', txt);
    return true;
  } catch {
    return false;
  }
}
