import { describe, it, expect } from "vitest";
import fc from "fast-check";

import { mapCitations, type DoanTraLoi } from "./citations.js";
import type { TrichDan } from "../types/index.js";

/**
 * Property test for the shared citation marker mapping (`mapCitations`), the single
 * pure logic both Web and Mobile use to turn an answer + `TrichDan[]` into segments.
 * Testing it here guarantees identical behaviour on both clients (R29.4) and that an
 * unknown marker never produces a broken link (R29.5).
 */

// A plain-text run that can never contain an accidental `[n]` marker: strip brackets.
const vanBanArb = fc.string().map((s) => s.replace(/[[\]]/g, ""));

/**
 * Generate, for a citation list of size N (markers 1..N), an answer string built from
 * a mix of plain text, VALID markers (in 1..N) and INVALID markers (0 or > N), plus the
 * derived facts the property checks against.
 */
const scenarioArb = fc.integer({ min: 1, max: 8 }).chain((N) => {
  const validMarker = fc.integer({ min: 1, max: N });
  const invalidMarker = fc.oneof(
    fc.constant(0),
    fc.integer({ min: N + 1, max: N + 10 }),
  );
  const tokenArb = fc.oneof(
    vanBanArb.map((text) => ({ kind: "text" as const, text })),
    validMarker.map((m) => ({ kind: "valid" as const, m })),
    invalidMarker.map((m) => ({ kind: "invalid" as const, m })),
  );
  return fc.record({
    N: fc.constant(N),
    tokens: fc.array(tokenArb, { maxLength: 30 }),
  });
});

describe("mapCitations (Property 33)", () => {
  // Feature: multi-user-rag-platform, Property 33: Marker trích dẫn nằm trong 1..N và song ánh với danh sách TrichDan
  // Validates: Requirements 29.4, 29.5
  it("links every emitted marker to an existing TrichDan in 1..N, drops unknown markers to plain text, and preserves the answer text", () => {
    fc.assert(
      fc.property(scenarioArb, ({ N, tokens }) => {
        // Citation list with markers exactly 1..N (the backend's 1-based convention).
        const trichDan: TrichDan[] = Array.from({ length: N }, (_, i) => ({
          marker: i + 1,
          chunkId: `chunk-${i + 1}`,
          taiLieuId: `tailieu-${i + 1}`,
          noiDung: `noi dung ${i + 1}`,
        }));
        const markersHopLe = new Set(trichDan.map((td) => td.marker));

        // Build the answer string and count how many VALID markers we embedded.
        let traLoi = "";
        let soMarkerHopLe = 0;
        for (const t of tokens) {
          if (t.kind === "text") {
            traLoi += t.text;
          } else {
            traLoi += `[${t.m}]`;
            if (t.kind === "valid") soMarkerHopLe += 1;
          }
        }

        const doan: DoanTraLoi[] = mapCitations(traLoi, trichDan);
        const doanTrichDan = doan.filter(
          (d): d is Extract<DoanTraLoi, { loai: "trichDan" }> =>
            d.loai === "trichDan",
        );

        // (R29.4/29.5) Every emitted citation segment links to a real TrichDan whose
        // marker is within 1..N — the within-range + bijection contract.
        for (const seg of doanTrichDan) {
          expect(markersHopLe.has(seg.marker)).toBe(true);
          expect(seg.trichDan).toBe(trichDan[seg.marker - 1]);
          expect(seg.trichDan.marker).toBe(seg.marker);
          expect(seg.noiDung).toBe(`[${seg.marker}]`);
        }

        // (R29.5) No unknown marker becomes a citation: exactly the valid markers we
        // embedded are emitted as `trichDan` segments — invalid ones stay in plain text.
        expect(doanTrichDan.length).toBe(soMarkerHopLe);

        // Round-trip integrity: concatenating all segments rebuilds the original answer
        // verbatim (no text lost, duplicated, or reordered).
        expect(doan.map((d) => d.noiDung).join("")).toBe(traLoi);
      }),
      { numRuns: 200 },
    );
  });
});
