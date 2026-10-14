import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Add app projects here (e.g. "apps/web") once they have their own vitest config.
    projects: [
      {
        test: {
          name: "tooling",
          include: ["tooling/**/*.test.ts"],
          environment: "node",
        },
      },
    ],
  },
});
