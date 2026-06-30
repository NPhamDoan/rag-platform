/**
 * Pure client-side question-length validation (task 15.3, R29.5; mirrors backend R6.3).
 *
 * `validateQuestionLength` lets Web and Mobile block an invalid `cauHoi` BEFORE calling
 * the API, using the SAME bounds (1..1000 characters after trimming whitespace) and the
 * same trim semantics as the backend `QueryPipeline.validateQuestion`, so both clients
 * reject/accept the same input. Pure + deterministic — no DOM, node, react or
 * react-native.
 */

/** Minimum cauHoi length after trimming (mirrors backend CAU_HOI_MIN). */
export const CAU_HOI_MIN = 1;
/** Maximum cauHoi length after trimming (mirrors backend CAU_HOI_MAX). */
export const CAU_HOI_MAX = 1000;

/** Result of {@link validateQuestionLength}. */
export interface KetQuaKiemTraCauHoi {
  /** Whether the trimmed question is within 1..1000 characters. */
  hopLe: boolean;
  /** The whitespace-trimmed question (what should be sent to the API when valid). */
  cauHoiDaCat: string;
  /** A Vietnamese error message when invalid; `undefined` when valid. */
  thongDiep?: string;
}

/** Error message: empty after trimming. */
const _CAU_HOI_RONG = "Cau hoi khong duoc de trong.";
/** Error message: exceeds the max length. */
const _CAU_HOI_QUA_DAI = `Cau hoi khong duoc vuot qua ${CAU_HOI_MAX} ky tu.`;

/**
 * Validate a question's length client-side.
 *
 * Trims surrounding whitespace, then checks the trimmed length is within
 * {@link CAU_HOI_MIN}..{@link CAU_HOI_MAX}. Returns the trimmed value so the caller can
 * submit exactly what was validated.
 *
 * @param cauHoi the raw question text.
 * @returns `{ hopLe, cauHoiDaCat, thongDiep? }` — invalid when empty after trimming or
 *          longer than {@link CAU_HOI_MAX}.
 */
export function validateQuestionLength(cauHoi: string): KetQuaKiemTraCauHoi {
  const daCat = cauHoi.trim();
  if (daCat.length < CAU_HOI_MIN) {
    return { hopLe: false, cauHoiDaCat: daCat, thongDiep: _CAU_HOI_RONG };
  }
  if (daCat.length > CAU_HOI_MAX) {
    return { hopLe: false, cauHoiDaCat: daCat, thongDiep: _CAU_HOI_QUA_DAI };
  }
  return { hopLe: true, cauHoiDaCat: daCat };
}
