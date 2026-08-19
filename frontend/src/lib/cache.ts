// Tiny in-memory cache for expensive per-batch GETs (dashboard, error
// classification). Both can take several seconds to build on a large batch,
// and the data doesn't change just because you switched tabs or navigated
// back to a batch you already opened -- so once a batchId's response is in
// hand, reuse it instead of re-hitting the backend. Cleared per key by
// whichever mutation actually invalidates that data (see callers).
const store = new Map<string, unknown>();

export function cacheGet<T>(key: string): T | undefined {
  return store.get(key) as T | undefined;
}

export function cacheSet<T>(key: string, value: T): void {
  store.set(key, value);
}

export function cacheInvalidate(key: string): void {
  store.delete(key);
}
