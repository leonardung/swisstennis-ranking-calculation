/** Umami analytics, enabled by the server's /api/config (UMAMI_SCRIPT_URL, UMAMI_WEBSITE_ID).
 * Page views are sent by hand: the app routes with the URL hash, which Umami does not follow. */

type Payload = Record<string, string | number>;
type Umami = { track: (event: unknown, data?: Payload) => void };
type Call = (u: Umami) => void;

let umami: Umami | null = null;
let pending: Call[] | null = [];

function send(call: Call): void {
  if (umami) call(umami);
  else pending?.push(call);
}

export async function initAnalytics(): Promise<void> {
  try {
    const res = await fetch("/api/config");
    const cfg = (await res.json()).analytics as { script: string; website_id: string } | null;
    if (!cfg) throw new Error("disabled");
    const s = document.createElement("script");
    s.defer = true;
    s.src = cfg.script;
    s.dataset.websiteId = cfg.website_id;
    s.dataset.autoTrack = "false";
    s.onload = () => {
      umami = (window as unknown as { umami?: Umami }).umami ?? null;
      pending?.forEach((c) => umami && c(umami));
      pending = null;
    };
    s.onerror = () => (pending = null);
    document.head.appendChild(s);
  } catch {
    pending = null; // analytics off or unreachable: drop calls
  }
}

/** A page view of the current hash route (#/player/1/stats → /player/1/stats), with the
 * current document title. */
export function trackView(): void {
  const url = window.location.hash.replace(/^#/, "") || "/";
  const title = document.title;
  send((u) => u.track((props: Payload) => ({ ...props, url, title })));
}

export function trackEvent(name: string, data?: Payload): void {
  send((u) => u.track(name, data));
}
