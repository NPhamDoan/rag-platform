/**
 * Pure citation marker mapping `[n]` <-> TrichDan (task 15.3, R29.4 / R29.5).
 *
 * `mapCitations` turns a synthesized answer string (`traLoi`) plus the backend
 * `TrichDan[]` into an ordered list of render-agnostic segments. Web and Mobile render
 * the segments their own way (a button, a chip, a link...) so BOTH platforms interpret
 * the same DTO identically (R29.4) — this module renders NOTHING.
 *
 * Correctness contract (the property tested as Property 33 in task 15.4):
 * - A marker `[n]` resolves to a citation ONLY when the `TrichDan[]` contains an entry
 *   whose `marker === n` (the backend's 1-based marker convention, where marker n points
 *   to the n-th source Chunk). The list itself defines the valid markers 1..N.
 * - A marker `[n]` with no matching `TrichDan` (n < 1, n > N, or otherwise absent) is
 *   left untouched INSIDE the surrounding plain text, so it renders as ordinary text and
 *   never produces a broken link, while the rest of the answer is unaffected (R29.5).
 *
 * Pure + deterministic: no DOM, node, react or react-native — same input, same output on
 * every platform, and trivially testable.
 */

import type { TrichDan } from "../types/index.js";

/** A plain-text run of the answer, rendered verbatim (may contain invalid markers). */
export interface DoanVanBan {
  loai: "vanBan";
  noiDung: string;
}

/** A resolved citation run: the literal `[n]` text linked to its source TrichDan. */
export interface DoanTrichDan {
  loai: "trichDan";
  /** The marker number `n` (matches `trichDan.marker`). */
  marker: number;
  /** The literal matched text, e.g. "[1]". */
  noiDung: string;
  /** The source citation this marker links to. */
  trichDan: TrichDan;
}

/** One segment of a mapped answer: plain text or a resolved citation. */
export type DoanTraLoi = DoanVanBan | DoanTrichDan;

/** Matches an inline citation marker like `[1]`, capturing the digits. */
const MAU_MARKER = /\[(\d+)\]/g;

/**
 * Map an answer string + its TrichDan list into ordered segments.
 *
 * @param traLoi   the synthesized answer containing inline `[n]` markers.
 * @param trichDan the backend citation list; `marker` is the 1-based citation index.
 * @returns ordered segments: `vanBan` (plain text) and `trichDan` (resolved citation).
 *          Markers without a matching TrichDan stay embedded in plain text (no link).
 */
export function mapCitations(
  traLoi: string,
  trichDan: readonly TrichDan[],
): DoanTraLoi[] {
  // Index citations by their marker (first entry wins; the backend emits unique markers).
  const theoMarker = new Map<number, TrichDan>();
  for (const td of trichDan) {
    if (!theoMarker.has(td.marker)) theoMarker.set(td.marker, td);
  }

  const doan: DoanTraLoi[] = [];
  let viTri = 0; // start of the not-yet-emitted slice of `traLoi`

  // Fresh regex (stateful `lastIndex`) so the function stays pure across calls.
  const re = new RegExp(MAU_MARKER.source, "g");
  let khop: RegExpExecArray | null;
  while ((khop = re.exec(traLoi)) !== null) {
    const toanBo = khop[0];
    const soChuoi = khop[1];
    if (toanBo === undefined || soChuoi === undefined) continue;

    const nguon = theoMarker.get(Number(soChuoi));
    // No matching TrichDan -> leave the marker inside the surrounding plain text (R29.5).
    if (nguon === undefined) continue;

    // Emit the plain text preceding this valid marker (includes any invalid markers).
    if (khop.index > viTri) {
      doan.push({ loai: "vanBan", noiDung: traLoi.slice(viTri, khop.index) });
    }
    doan.push({
      loai: "trichDan",
      marker: nguon.marker,
      noiDung: toanBo,
      trichDan: nguon,
    });
    viTri = re.lastIndex;
  }

  // Emit the trailing plain text after the last valid marker.
  if (viTri < traLoi.length) {
    doan.push({ loai: "vanBan", noiDung: traLoi.slice(viTri) });
  }
  return doan;
}
