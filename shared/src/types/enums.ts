/**
 * Enums shared between Web and Mobile, mirroring `backend/app/db/models.py` exactly.
 *
 * Each enum is expressed as a frozen const object (runtime values) plus a derived
 * string-union type (compile-time). The string VALUES must match the backend
 * `enum.value` byte-for-byte — in particular `NhanXacMinh` keeps its Vietnamese
 * diacritics, while the account/permission/document enums are diacritics-free.
 */

/** Account role (R1, R10). */
export const VaiTro = {
  NGUOI_DUNG: "NGUOI_DUNG",
  QUAN_TRI: "QUAN_TRI",
} as const;
export type VaiTro = (typeof VaiTro)[keyof typeof VaiTro];

/** Account status (R2, R10). */
export const TrangThaiTaiKhoan = {
  HOAT_DONG: "HOAT_DONG",
  VO_HIEU_HOA: "VO_HIEU_HOA",
} as const;
export type TrangThaiTaiKhoan =
  (typeof TrangThaiTaiKhoan)[keyof typeof TrangThaiTaiKhoan];

/** Workspace share permission level (R11). */
export const MucQuyen = {
  CHI_DOC: "CHI_DOC",
  GHI: "GHI",
} as const;
export type MucQuyen = (typeof MucQuyen)[keyof typeof MucQuyen];

/** Document state machine NAP -> PARSE -> DA_PARSE_CHO_DUYET -> DA_EMBED (R5, R18). */
export const TrangThaiTaiLieu = {
  NAP: "NAP",
  PARSE: "PARSE",
  DA_PARSE_CHO_DUYET: "DA_PARSE_CHO_DUYET",
  DA_EMBED: "DA_EMBED",
} as const;
export type TrangThaiTaiLieu =
  (typeof TrangThaiTaiLieu)[keyof typeof TrangThaiTaiLieu];

/**
 * Cross-verification label (R7, R8, R16). The string values intentionally KEEP
 * their Vietnamese diacritics to match the backend `NhanXacMinh` enum values.
 */
export const NhanXacMinh = {
  DA_XAC_MINH: "đã xác minh",
  CO_MAU_THUAN: "có mâu thuẫn",
  CHUA_XAC_MINH: "chưa xác minh",
} as const;
export type NhanXacMinh = (typeof NhanXacMinh)[keyof typeof NhanXacMinh];
