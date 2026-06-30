/**
 * Pure history cap helper (task 15.3, R29.3).
 *
 * `capHistory` caps a locally held chat/history list to the most recent
 * `GIOI_HAN_LICH_SU` (50) entries, mirroring the backend which retains the 50 most
 * recent LichSuTroChuyen entries — so Web and Mobile keep the same bounded history.
 *
 * Generic over the element type so it works for the chat `Message[]` list as well as a
 * `HistoryItemResponse[]`. Pure + deterministic (returns a NEW array, never mutates the
 * input) — no DOM, node, react or react-native.
 */

/** Default cap on locally retained history entries (R29.3). */
export const GIOI_HAN_LICH_SU = 50;

/**
 * Keep only the most recent `gioiHan` entries of a history/chat list.
 *
 * Assumes chronological ascending order (oldest first, newest last) — matching the chat
 * `Message[]` list to which new messages are appended — so the tail (the newest entries)
 * is retained, consistent with the backend keeping the 50 most recent.
 *
 * @param danhSach the history/chat list (not mutated).
 * @param gioiHan  max entries to keep; defaults to {@link GIOI_HAN_LICH_SU}.
 * @returns a new array of at most `gioiHan` most-recent entries.
 */
export function capHistory<T>(
  danhSach: readonly T[],
  gioiHan: number = GIOI_HAN_LICH_SU,
): T[] {
  if (gioiHan <= 0) return [];
  if (danhSach.length <= gioiHan) return danhSach.slice();
  return danhSach.slice(danhSach.length - gioiHan);
}
