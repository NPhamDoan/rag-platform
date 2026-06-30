import { defineConfig } from "vitest/config";

/**
 * Vitest config for the platform-agnostic shared package.
 * - `node` environment: this package must not depend on the DOM (Web/Mobile neutral).
 * - Picks up co-located `*.test.ts` specs under `src/`.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
