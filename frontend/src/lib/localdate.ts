/**
 * Today's date as the person at the keyboard sees it.
 *
 * `toISOString().slice(0, 10)` gives the UTC date, which in Nepal (UTC+05:45)
 * is yesterday for the whole first stretch of the working morning. Anyone
 * opening the app at 5am was shown the previous day's settlements and told
 * that was today.
 */
export function localIso(d: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** `days` before today, in local time. */
export function localIsoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return localIso(d);
}
