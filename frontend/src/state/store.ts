import { create } from "zustand";
import { ApiError, api, type Profile, type Theme } from "../api/client";

const CSS_VARS: Record<string, string> = {
  bg: "--bg",
  bgElev: "--bg-elev",
  bgPanel: "--bg-panel",
  bgHover: "--bg-hover",
  border: "--border",
  borderStrong: "--border-strong",
  text: "--text",
  textStrong: "--text-strong",
  textDim: "--text-dim",
  textFaint: "--text-faint",
  accent: "--accent",
  accentHover: "--accent-hover",
  accentBg: "--accent-bg",
  selection: "--selection",
  ok: "--ok",
  warn: "--warn",
  err: "--err",
  termBg: "--term-bg",
  termFg: "--term-fg",
  termCursor: "--term-cursor",
  termSel: "--term-sel",
  statusBg: "--status-bg",
  statusFg: "--status-fg",
  buttonBg: "--button-bg",
  buttonHover: "--button-hover",
  buttonBorder: "--button-border",
  errorBg: "--error-bg",
  errorFg: "--error-fg",
  selectionText: "--selection-text",
  focusRing: "--focus-ring",
};

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  for (const [key, cssVar] of Object.entries(CSS_VARS)) {
    const value = theme[key];
    if (value) root.style.setProperty(cssVar, value);
  }
  applyWindowOpacity(theme.opacity);
  applyTitlebar(theme);
}

interface Bridge {
  setOpacity?: (value: string) => void;
  setTitlebar?: (bg: string, fg: string, border: string) => void;
}

function bridge(): Bridge | undefined {
  return (window as unknown as { __bridge?: Bridge }).__bridge;
}

function applyWindowOpacity(raw: string | undefined, attempt = 0): void {
  if (!raw) return;
  const b = bridge();
  if (b?.setOpacity) {
    b.setOpacity(raw);
    return;
  }
  if (attempt < 20) {
    window.setTimeout(() => applyWindowOpacity(raw, attempt + 1), 500);
  }
}

function applyTitlebar(theme: Theme, attempt = 0): void {
  if (!theme.bg) return;
  const b = bridge();
  if (b?.setTitlebar) {
    b.setTitlebar(theme.bg, theme.text ?? theme.bg, theme.border ?? theme.bg);
    return;
  }
  if (attempt < 20) {
    window.setTimeout(() => applyTitlebar(theme, attempt + 1), 500);
  }
}

export interface Tab {
  id: string;
  sessionId: string;
  profileId: string;
  title: string;
  protocol: "ssh" | "serial";
  state: string;
  error: string | null;
  exitCode: number | null;
}

interface HostkeyPrompt {
  sessionId: string;
  host: string;
  port: number;
  key_type: string;
  fingerprint: string;
}

interface KbdintPrompt {
  sessionId: string;
  name: string;
  instruction: string;
  prompts: [string, boolean][];
}

interface AppState {
  loaded: boolean;
  profiles: Profile[];
  folders: { id: string; name: string }[];
  workspacePath: string;
  dirty: boolean;
  conflict: boolean;
  tabs: Tab[];
  activeId: string | null;
  error: string | null;
  notice: string | null;
  setNotice: (message: string | null) => void;
  theme: Theme;
  themePath: string;
  loadTheme: () => Promise<void>;
  saveTheme: (theme: Theme) => Promise<void>;
  resetTheme: () => Promise<void>;
  previewTheme: (theme: Theme) => void;
  hostkey: HostkeyPrompt | null;
  kbdint: KbdintPrompt | null;
  passwordFor: Profile | null;
  passwordResolver: ((value: string | null) => void) | null;
  confirmMessage: string | null;
  confirmResolver: ((ok: boolean) => void) | null;
  promptMessage: string | null;
  promptValue: string;
  promptResolver: ((value: string | null) => void) | null;
  askConfirm: (message: string) => Promise<boolean>;
  submitConfirm: (ok: boolean) => void;
  askPrompt: (message: string, initial?: string) => Promise<string | null>;
  submitPrompt: (value: string | null) => void;

  init: () => Promise<void>;
  refresh: () => Promise<void>;
  openSession: (profile: Profile) => Promise<void>;
  closeTab: (tabId: string) => Promise<void>;
  setActive: (tabId: string) => void;
  patchTab: (tabId: string, patch: Partial<Tab>) => void;
  detachTab: (tabId: string) => void;
  addAttached: (tab: Omit<Tab, "id" | "state" | "error" | "exitCode">) => void;
  saveProfile: (profile: Profile) => Promise<void>;
  deleteProfile: (id: string) => Promise<void>;
  resolveHostkey: (decision: "once" | "always" | "reject") => Promise<void>;
  resolveKbdint: (responses: string[]) => Promise<void>;
  askPassword: (profile: Profile) => Promise<string | null>;
  submitPassword: (value: string | null) => void;
  setError: (code: string | null) => void;
  showHostkey: (prompt: HostkeyPrompt) => void;
  showKbdint: (prompt: KbdintPrompt) => void;
}

export const useApp = create<AppState>((set, get) => ({
  loaded: false,
  profiles: [],
  folders: [],
  workspacePath: "",
  dirty: false,
  conflict: false,
  tabs: [],
  activeId: null,
  error: null,
  notice: null,
  theme: {},
  themePath: "",
  hostkey: null,
  kbdint: null,
  passwordFor: null,
  passwordResolver: null,
  confirmMessage: null,
  confirmResolver: null,
  promptMessage: null,
  promptValue: "",
  promptResolver: null,

  init: async () => {
    await get().refresh();
    set({ loaded: true });
  },

  refresh: async () => {
    try {
      const data = await api.list();
      set({
        profiles: data.workspace.profiles,
        folders: data.workspace.folders,
        workspacePath: data.path,
        dirty: data.dirty,
        conflict: data.conflict,
      });
    } catch (err) {
      if (err instanceof ApiError) set({ error: err.code });
    }
  },

  openSession: async (profile) => {
    try {
      let password: string | undefined;
      if (profile.protocol === "ssh" &&
          (profile.auth.method === "password" || profile.auth.method === "kbdint")) {
        const exists = profile.auth.credential_id
          ? (await api.hasSecret(profile.auth.credential_id)).exists
          : false;
        if (!exists) {
          const entered = await get().askPassword(profile);
          if (!entered) return;
          password = entered;
        }
      }
      const session =
        profile.protocol === "serial"
          ? await api.createSerialSession(profile.id)
          : await api.createSession(profile.id, password);
      const tab: Tab = {
        id: session.session_id,
        sessionId: session.session_id,
        profileId: profile.id,
        title: profile.name,
        protocol: profile.protocol,
        state: session.state,
        error: (session as { error?: string | null }).error ?? null,
        exitCode: null,
      };
      set({ tabs: [...get().tabs, tab], activeId: tab.id });
    } catch (err) {
      if (err instanceof ApiError) set({ error: err.code });
    }
  },

  closeTab: async (tabId) => {
    const tab = get().tabs.find((t) => t.id === tabId);
    const tabs = get().tabs.filter((t) => t.id !== tabId);
    const activeId = get().activeId === tabId ? (tabs.length ? tabs[tabs.length - 1].id : null) : get().activeId;
    set({ tabs, activeId });
    if (tab) {
      try {
        await api.deleteSession(tab.sessionId);
      } catch {
        /* already gone */
      }
    }
  },

  setActive: (tabId) => set({ activeId: tabId }),

  patchTab: (tabId, patch) =>
    set({ tabs: get().tabs.map((t) => (t.id === tabId ? { ...t, ...patch } : t)) }),

  detachTab: (tabId) => {
    const tabs = get().tabs.filter((t) => t.id !== tabId);
    const activeId =
      get().activeId === tabId ? (tabs.length ? tabs[tabs.length - 1].id : null) : get().activeId;
    set({ tabs, activeId });
  },

  addAttached: (tab) => {
    const full: Tab = { ...tab, id: tab.sessionId, state: "idle", error: null, exitCode: null };
    const tabs = [...get().tabs.filter((t) => t.id !== full.id), full];
    set({ tabs, activeId: full.id });
  },

  saveProfile: async (profile) => {
    try {
      const exists = get().profiles.some((p) => p.id === profile.id);
      if (exists) {
        await api.updateProfile(profile);
      } else {
        await api.createProfile(profile);
      }
      await get().refresh();
    } catch (err) {
      if (err instanceof ApiError) set({ error: err.code });
      throw err;
    }
  },

  deleteProfile: async (id) => {
    try {
      await api.deleteProfile(id);
      await get().refresh();
    } catch (err) {
      if (err instanceof ApiError) set({ error: err.code });
    }
  },

  resolveHostkey: async (decision) => {
    const prompt = get().hostkey;
    set({ hostkey: null });
    if (!prompt) return;
    try {
      await api.hostkey(prompt.sessionId, decision);
    } catch (err) {
      if (err instanceof ApiError) set({ error: err.code });
    }
  },

  resolveKbdint: async (responses) => {
    const prompt = get().kbdint;
    set({ kbdint: null });
    if (!prompt) return;
    try {
      await api.kbdint(prompt.sessionId, responses);
    } catch (err) {
      if (err instanceof ApiError) set({ error: err.code });
    }
  },

  askPassword: (profile) =>
    new Promise<string | null>((resolve) => {
      set({ passwordFor: profile, passwordResolver: resolve });
    }),

  submitPassword: (value) => {
    const resolver = get().passwordResolver;
    set({ passwordFor: null, passwordResolver: null });
    resolver?.(value);
  },

  setError: (code) => set({ error: code }),

  askConfirm: (message) =>
    new Promise<boolean>((resolve) => {
      set({ confirmMessage: message, confirmResolver: resolve });
    }),

  submitConfirm: (ok) => {
    const resolver = get().confirmResolver;
    set({ confirmMessage: null, confirmResolver: null });
    resolver?.(ok);
  },

  askPrompt: (message, initial = "") =>
    new Promise<string | null>((resolve) => {
      set({ promptMessage: message, promptValue: initial, promptResolver: resolve });
    }),

  submitPrompt: (value) => {
    const resolver = get().promptResolver;
    set({ promptMessage: null, promptValue: "", promptResolver: null });
    resolver?.(value);
  },

  loadTheme: async () => {
    try {
      const data = await api.getTheme();
      applyTheme(data.theme);
      set({ theme: data.theme, themePath: data.path });
    } catch {
      /* keep bootstrapped defaults */
    }
  },

  previewTheme: (theme) => {
    applyTheme(theme);
    set({ theme });
  },

  saveTheme: async (theme) => {
    const data = await api.saveTheme(theme);
    applyTheme(data.theme);
    set({ theme: data.theme, themePath: data.path });
  },

  resetTheme: async () => {
    const data = await api.resetTheme();
    applyTheme(data.theme);
    set({ theme: data.theme, themePath: data.path });
  },

  setNotice: (message) => {
    set({ notice: message });
    if (message) {
      window.setTimeout(() => {
        if (get().notice === message) set({ notice: null });
      }, 5000);
    }
  },
  showHostkey: (prompt) => set({ hostkey: prompt }),
  showKbdint: (prompt) => set({ kbdint: prompt }),
}));
