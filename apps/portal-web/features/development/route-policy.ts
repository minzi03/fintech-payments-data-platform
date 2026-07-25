export function developmentRoutesEnabled(environment = process.env.NODE_ENV): boolean {
  return environment !== "production";
}
