/** The server's /api/config, fetched once. */

export interface AppConfig {
  analytics: { script: string; website_id: string } | null;
  moved_to: string | null;
}

export const appConfig: Promise<AppConfig> = fetch("/api/config").then((r) => {
  if (!r.ok) throw new Error(`config: ${r.status}`);
  return r.json();
});
