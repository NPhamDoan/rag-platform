/**
 * Public entry point for `@rag-platform/shared`.
 *
 * Re-exports the platform-agnostic contract consumed by both the Web (React + Vite)
 * and Mobile (React Native + Expo) clients:
 * - `types/`   — DTOs + enums mirroring the backend (task 15.1).
 * - `api/`     — `createApiClient` factory + typed errors (task 15.2).
 * - `utils/`   — framework-agnostic helpers (logging contract).
 * - `logic/`   — pure shared business logic: citation mapping, history cap,
 *                question-length validation (task 15.3).
 */

export * from "./types/index.js";
export * from "./api/client.js";
export * from "./utils/logger.js";
export * from "./logic/index.js";
