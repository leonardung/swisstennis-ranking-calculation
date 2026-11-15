/** The server's /api/config, fetched once. */

export interface AppConfig {
  analytics: { script: string; website_id: string } | null;
  moved_to: string | null;
  /** user accounts on this address (only tennis-radar.ch), with or without Google sign-in */
  accounts: { google: boolean } | null;
  /** the data is for signed-in users only (on every address; only tennis-radar.ch has sign-in) */
  login_required: boolean;
}

export const appConfig: Promise<AppConfig> = fetch("/api/config").then((r) => {
  if (!r.ok) throw new Error(`config: ${r.status}`);
  return r.json();
});
