import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// Explicit cleanup since this config doesn't enable Vitest's `globals`
// (test files import `describe`/`it`/`expect` explicitly instead).
afterEach(() => {
  cleanup();
});
