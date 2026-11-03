/**
 * Scans waiting for the network (L9). A count goes on while the shed has
 * no signal: each scan is kept on the device, in IndexedDB, with a key made
 * here, and sent when the network returns. The server records a key once,
 * so a scan sent twice (a retry after a cut, a second tab) counts once.
 *
 * IndexedDB can be missing or refuse (a private window, storage cleared or
 * blocked): the queue then lives in memory, so the screen still works
 * online and says what a closed tab would lose.
 */

export interface QueuedScan {
  key: string;
  /** The endpoint it is sent to, e.g. /api/inventory/stock-counts/7/scan/ */
  path: string;
  body: Record<string, unknown>;
  /** What the person scanned, for the waiting list: "A × 5 in N-01". */
  label: string;
  at: number;
}

const NAME = "erp-scans";
const STORE = "queue";
let memory: QueuedScan[] = [];
let database: Promise<IDBDatabase | null> | null = null;

function open(): Promise<IDBDatabase | null> {
  if (database) return database;
  database = new Promise((resolve) => {
    try {
      if (typeof indexedDB === "undefined") return resolve(null);
      const asked = indexedDB.open(NAME, 1);
      asked.onupgradeneeded = () => {
        if (!asked.result.objectStoreNames.contains(STORE)) asked.result.createObjectStore(STORE, { keyPath: "key" });
      };
      asked.onsuccess = () => resolve(asked.result);
      asked.onerror = () => resolve(null);
      asked.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return database;
}

/** Whether the queue survives closing the tab (false in a private window). */
export async function kept(): Promise<boolean> {
  return (await open()) !== null;
}

function run<T>(db: IDBDatabase, mode: IDBTransactionMode, work: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = work(db.transaction(STORE, mode).objectStore(STORE));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function enqueue(scan: QueuedScan): Promise<void> {
  const db = await open();
  if (!db) {
    memory = [...memory.filter((row) => row.key !== scan.key), scan];
    return;
  }
  try {
    await run(db, "readwrite", (store) => store.put(scan));
  } catch {
    memory = [...memory.filter((row) => row.key !== scan.key), scan];
  }
}

/** What is waiting for `path`, oldest first. */
export async function waiting(path: string): Promise<QueuedScan[]> {
  const db = await open();
  let stored: QueuedScan[] = [];
  if (db) {
    try {
      stored = await run<QueuedScan[]>(db, "readonly", (store) => store.getAll() as IDBRequest<QueuedScan[]>);
    } catch {
      stored = [];
    }
  }
  return [...stored, ...memory].filter((row) => row.path === path).sort((a, b) => a.at - b.at);
}

export async function drop(key: string): Promise<void> {
  memory = memory.filter((row) => row.key !== key);
  const db = await open();
  if (!db) return;
  try {
    await run(db, "readwrite", (store) => store.delete(key));
  } catch {
    // Already gone, or storage refused: the server records the key once either way.
  }
}

/** A key for one scan, made on the device. */
export function scanKey(): string {
  const maker = typeof crypto !== "undefined" ? crypto : undefined;
  if (maker?.randomUUID) return maker.randomUUID();
  const bytes = new Uint8Array(16);
  if (maker?.getRandomValues) maker.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
