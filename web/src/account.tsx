import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { trackEvent } from "./analytics";
import { accountApi, ApiError } from "./api";
import { appConfig } from "./config";
import type { MePlayer, User } from "./types";

const RETRY_MS = 4000;

/** The visitor's account. Everything lives on the server: nothing is kept in the browser but
 * the session cookie (HttpOnly). Accounts exist only on tennis-radar.ch (`enabled`). */
export interface Account {
  enabled: boolean;
  google: boolean;
  /** the configuration and the signed-in user are known (pages start their filters from "me") */
  ready: boolean;
  user: User | null;
  me: MePlayer | null;
  /** fetch the user again; resolves with it */
  refresh: () => Promise<User | null>;
  isFavourite: (id: number) => boolean;
  toggleFavourite: (id: number) => Promise<void>;
  setMe: (id: number | null) => Promise<void>;
  logout: () => Promise<void>;
  deleteAccount: () => Promise<void>;
  /** the sign-in dialog */
  loginOpen: boolean;
  openLogin: () => void;
  closeLogin: () => void;
}

export const AccountContext = createContext<Account | null>(null);

export function useAccount(): Account {
  const a = useContext(AccountContext);
  if (!a) throw new Error("useAccount outside AccountContext");
  return a;
}

export function useAccountState(): Account {
  // undefined: not known yet
  const [config, setConfig] = useState<{ google: boolean } | null | undefined>(undefined);
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [loginOpen, setLoginOpen] = useState(false);

  useEffect(() => {
    appConfig.then((c) => setConfig(c.accounts ?? null)).catch(() => setConfig(null));
  }, []);

  const refresh = useCallback(async (): Promise<User | null> => {
    for (;;) {
      try {
        const u = (await accountApi.me()).user;
        setUser(u);
        return u;
      } catch (e) {
        // the server is still loading its data: try again
        if (e instanceof ApiError && e.loading) {
          await new Promise((r) => window.setTimeout(r, RETRY_MS));
          continue;
        }
        setUser(null);
        return null;
      }
    }
  }, []);

  useEffect(() => {
    if (config) void refresh();
    else if (config === null) setUser(null);
  }, [config, refresh]);

  return useMemo<Account>(() => {
    const favourites = new Set(user?.favourites ?? []);
    return {
      enabled: !!config,
      google: !!config?.google,
      ready: config !== undefined && user !== undefined,
      user: user ?? null,
      me: user?.me ?? null,
      refresh,
      isFavourite: (id) => favourites.has(id),
      toggleFavourite: async (id) => {
        if (!user) return;
        const on = !favourites.has(id);
        await accountApi.favourite(id, on);
        setUser({ ...user, favourites: on ? [...user.favourites, id] : user.favourites.filter((x) => x !== id) });
      },
      setMe: async (id) => {
        if (!user) return;
        const { me } = await accountApi.setMe(id);
        setUser({ ...user, me });
      },
      logout: async () => {
        await accountApi.logout();
        setUser(null);
      },
      deleteAccount: async () => {
        await accountApi.deleteAccount();
        setUser(null);
      },
      loginOpen,
      // counted, to compare with the sign-ups and sign-ins (who looked and left)
      openLogin: () => {
        trackEvent("login-open");
        setLoginOpen(true);
      },
      closeLogin: () => setLoginOpen(false),
    };
  }, [config, user, refresh, loginOpen]);
}
