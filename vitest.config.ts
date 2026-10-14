import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: "tooling",
          include: ["tooling/**/*.test.ts"],
          environment: "node",
        },
      },
      "apps/web/vitest.config.ts",
    ],
  },
});
