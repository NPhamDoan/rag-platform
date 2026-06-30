/**
 * Pure shared business logic (task 15.3) — reused identically by Web and Mobile so the
 * two clients behave consistently (R29):
 * - `citations` — map `[n]` markers <-> TrichDan into render-agnostic segments (R29.4/5).
 * - `history`   — cap a chat/history list to the 50 most recent entries (R29.3).
 * - `question`  — validate cauHoi length 1..1000 client-side (R29.5 / R6.3).
 *
 * Every export is a pure, deterministic, dependency-free function.
 */

export * from "./citations.js";
export * from "./history.js";
export * from "./question.js";
