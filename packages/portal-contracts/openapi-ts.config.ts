import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./openapi/portal-api-v1.json",
  output: {
    path: "./src/generated",
    postProcess: ["prettier"],
  },
  plugins: [
    "@hey-api/typescript",
    {
      name: "@hey-api/sdk",
      operations: {
        strategy: "single",
      },
    },
    {
      name: "@hey-api/client-fetch",
      runtimeConfigPath: "./src/client-config.ts",
    },
  ],
});
