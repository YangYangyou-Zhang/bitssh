import { useEffect, useState, type ChangeEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, TOKEN, api, type Profile } from "./api/client";
import AppearanceDialog from "./components/AppearanceDialog";
import ForwardPanel from "./components/ForwardPanel";
import KnownHostsDialog from "./components/KnownHostsDialog";
import Sidebar from "./components/Sidebar";
import SftpPanel from "./components/SftpPanel";
import TerminalPane from "./components/TerminalPane";
import ProfileEditor from "./components/ProfileEditor";
import {
  ConfirmDialog,
  ErrorToast,
  HostKeyDialog,
  KbdintDialog,
  NoticeToast,
  PasswordDialog,
  PromptDialog,
} from "./components/Dialogs";
import { setLanguage } from "./i18n";
import { useApp, type Tab } from "./state/store";

const HASH = new URLSearchParams(location.hash.startsWith("#") ? location.hash.slice(1) : "");
const SINGLE_MODE = HASH.get("mode") === "single";
const ATTACH_SESSION = HASH.get("attach");

export default function App() {
  const { t, i18n } = useTranslation();
  const init = useApp((s) => s.init);
  const tabs = useApp((s) => s.tabs);
  const activeId = useApp((s) => s.activeId);
  const setActive = useApp((s) => s.setActive);
  const closeTab = useApp((s) => s.closeTab);
  const workspacePath = useApp((s) => s.workspacePath);
  const conflict = useApp((s) => s.conflict);

  const [editing, setEditing] = useState<Profile | "new" | null>(null);
  const [showSftp, setShowSftp] = useState(false);
  const [showAppearance, setShowAppearance] = useState(false);
  const [showKnownHosts, setShowKnownHosts] = useState(false);
  const loadTheme = useApp((s) => s.loadTheme);
  const [showForwards, setShowForwards] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() =>
    Number(localStorage.getItem("bitssh.ui.sidebarWidth") || 290),
  );
  const [panelWidth, setPanelWidth] = useState(() =>
    Number(localStorage.getItem("bitssh.ui.panelWidth") || 430),
  );

  const startColumnDrag = (
    kind: "sidebar" | "panel",
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    event.preventDefault();
    const startX = event.clientX;
    const start = kind === "sidebar" ? sidebarWidth : panelWidth;
    const onMove = (move: PointerEvent) => {
      const delta = move.clientX - startX;
      const next =
        kind === "sidebar"
          ? Math.max(180, Math.min(560, start + delta))
          : Math.max(300, Math.min(1000, start - delta));
      if (kind === "sidebar") {
        setSidebarWidth(next);
        localStorage.setItem("bitssh.ui.sidebarWidth", String(next));
      } else {
        setPanelWidth(next);
        localStorage.setItem("bitssh.ui.panelWidth", String(next));
      }
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };
  const [renderer, setRenderer] = useState(localStorage.getItem("bitssh.renderer") || "dom");

  const addAttached = useApp((s) => s.addAttached);

  useEffect(() => {
    (window as unknown as { __store: typeof useApp }).__store = useApp;
    void loadTheme();
    if (ATTACH_SESSION) {
      addAttached({
        sessionId: ATTACH_SESSION,
        profileId: HASH.get("profile_id") || "",
        title: HASH.get("profile") || "bitssh",
        protocol: (HASH.get("protocol") as "ssh" | "serial") || "ssh",
      });
      return;
    }
    void init();
  }, [init, addAttached, loadTheme]);

  const active = tabs.find((tab) => tab.id === activeId) ?? null;

  function openInNewWindow(tab: Tab) {
    const query = new URLSearchParams({
      token: TOKEN,
      attach: tab.sessionId,
      mode: "single",
      profile: tab.title,
      protocol: tab.protocol,
      profile_id: tab.profileId,
    });
    const popup = window.open(`${location.origin}/#${query.toString()}`, "_blank");
    if (popup) {
      useApp.getState().detachTab(tab.id);
    }
  }

  async function handleImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      await api.importProfiles(payload, "copy");
      await useApp.getState().refresh();
    } catch (err) {
      useApp.getState().setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  async function handleExport() {
    try {
      const data = await api.exportProfiles();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "bitssh-export.bitssh.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) {
      useApp.getState().setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">bitssh</span>
        {SINGLE_MODE && (
          <button
            onClick={() => {
              const tab = useApp.getState().tabs[0];
              if (!tab) return;
              const query = new URLSearchParams({
                session: tab.sessionId,
                profile: tab.title,
                protocol: tab.protocol,
                profile_id: tab.profileId,
              });
              window.location.href = `/dialog/redock?${query.toString()}`;
            }}
          >
            {t("window.redock")}
          </button>
        )}
        {!SINGLE_MODE && (
          <>
            <button onClick={() => { window.location.href = "/dialog/open"; }}>{t("menu.open")}</button>
            <button onClick={() => { window.location.href = "/dialog/save"; }}>{t("menu.saveAs")}</button>
            <label className="filebtn">
              {t("menu.import")}
              <input type="file" accept=".json,.bitssh.json" hidden onChange={(e) => void handleImport(e)} />
            </label>
            <button onClick={() => void handleExport()}>{t("menu.export")}</button>
          </>
        )}
        <button
          className={showSftp ? "primary" : ""}
          disabled={!active || active.protocol === "serial"}
          onClick={() => {
            setShowSftp((v) => !v);
            setShowForwards(false);
          }}
        >
          {t("sftp.panel")}
        </button>
        <button
          className={showForwards ? "primary" : ""}
          disabled={!active || active.protocol === "serial"}
          onClick={() => {
            setShowForwards((v) => !v);
            setShowSftp(false);
          }}
        >
          {t("fwd.panel")}
        </button>
        <button onClick={() => setShowKnownHosts(true)}>{t("knownHosts.title")}</button>
        <button onClick={() => setShowAppearance(true)}>{t("appearance.title")}</button>
        <span className="spacer" />
        <label className="inline">
          {t("status.language")}
          <select value={i18n.language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="zh-CN">中文</option>
            <option value="en-US">English</option>
          </select>
        </label>
        <label className="inline">
          {t("status.renderer")}
          <select
            value={renderer}
            onChange={(e) => {
              localStorage.setItem("bitssh.renderer", e.target.value);
              setRenderer(e.target.value);
            }}
          >
            <option value="dom">DOM</option>
            <option value="webgl">WebGL</option>
          </select>
        </label>
      </header>

      <div className="body">
        {!SINGLE_MODE && (
          <>
            <div className="sidebar-host" style={{ width: sidebarWidth }}>
              <Sidebar onNew={() => setEditing("new")} onEdit={(profile) => setEditing(profile)} />
            </div>
            <div
              className="vsplitter"
              onPointerDown={(event) => startColumnDrag("sidebar", event)}
            />
          </>
        )}
        <main className="main">
          {!SINGLE_MODE && (
            <div className="tabbar">
              {tabs.map((tab) => (
                <div
                  key={tab.id}
                  className={"tab" + (tab.id === activeId ? " active" : "")}
                  onClick={() => setActive(tab.id)}
                >
                  <span className="tab-title">{tab.title}</span>
                  <span className={"dot " + tab.state} />
                  <button
                    className="tab-close"
                    title="Open in new window"
                    onClick={(e) => {
                      e.stopPropagation();
                      openInNewWindow(tab);
                    }}
                  >
                    ⧉
                  </button>
                  <button
                    className="tab-close"
                    onClick={(e) => {
                      e.stopPropagation();
                      void closeTab(tab.id);
                    }}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="panes">
            {tabs.map((tab) => (
              <div
                key={tab.id}
                className={"pane-host" + (tab.id === activeId ? " active" : "")}
              >
                <TerminalPane tab={tab} />
              </div>
            ))}
            {tabs.length === 0 && <div className="welcome">{t("app.noSession")}</div>}
          </div>
        </main>
        {((showSftp && active && active.protocol === "ssh") ||
          (showForwards && active && active.protocol === "ssh")) && (
          <div
            className="vsplitter"
            onPointerDown={(event) => startColumnDrag("panel", event)}
          />
        )}
        {showSftp && active && active.protocol === "ssh" && (
          <div className="side-panel" style={{ width: panelWidth }}>
            <SftpPanel
              key={active.sessionId}
              sessionId={active.sessionId}
              profileId={active.profileId}
            />
          </div>
        )}
        {showForwards && active && active.protocol === "ssh" && (
          <div className="side-panel" style={{ width: panelWidth }}>
            <ForwardPanel key={active.sessionId} sessionId={active.sessionId} />
          </div>
        )}
      </div>

      <footer className="statusbar" hidden={SINGLE_MODE}>
        <span>
          {t("status.workspace")}: {workspacePath || "-"}
        </span>
        {conflict && <span className="warn">{t("status.conflict")}</span>}
      </footer>

      {editing && (
        <ProfileEditor
          profile={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      )}
      <HostKeyDialog />
      <KbdintDialog />
      <PasswordDialog />
      <ErrorToast />
      <NoticeToast />
      <ConfirmDialog />
      <PromptDialog />
      {showAppearance && <AppearanceDialog onClose={() => setShowAppearance(false)} />}
      {showKnownHosts && <KnownHostsDialog onClose={() => setShowKnownHosts(false)} />}
    </div>
  );
}
