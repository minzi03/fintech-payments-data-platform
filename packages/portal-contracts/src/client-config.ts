import type { CreateClientConfig } from "./generated/client.gen";

export const createClientConfig: CreateClientConfig = (config) => ({
  ...config,
  credentials: "same-origin",
});
