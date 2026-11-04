import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type KnownHost } from "../api/client";
import { useApp } from "../state/store";

export default function KnownHostsDialog({ onClose }: { onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const [items, setItems] = useState<KnownHost[]>([]);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const data = await api.knownHosts();
      setItems(data.known_hosts);
      setPath(data.path);
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const remove = async (item: KnownHost) => {
    const ok = await useApp.getState().askConfirm(
      t("knownHosts.deleteConfirm", {
        host: item.host,
        port: item.port,
        type: item.key_type,
      }),
    );
    if (!ok) return;
    setBusy(true);
    try {
      const data = await api.deleteKnownHost(item.host, item.port, item.key_type);
      setItems(data.known_hosts);
      setPath(data.path);
      useApp.getState().setNotice(t("knownHosts.deleted"));
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    } finally {
      setBusy(false);
    }
  };

  const when = (iso: string) => {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(i18n.language);
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal known-hosts" onMouseDown={(event) => event.stopPropagation()}>
        <h2>{t("knownHosts.title")}</h2>
        <p className="appearance-hint">{t("knownHosts.hint", { path: path || "…" })}</p>
        {error && (
          <div className="pane-error">
            {t(`errors.${error}`, { defaultValue: t("errors.UNKNOWN") })}
          </div>
        )}
        <div className="known-host-list">
          {items.length === 0 && <div className="empty">{t("knownHosts.empty")}</div>}
          {items.map((item) => (
            <div
              className="known-host-row"
              key={`${item.host}:${item.port}:${item.key_type}`}
            >
              <div className="known-host-main">
                <div className="known-host-name">
                  {item.host}:{item.port}
                </div>
                <div className="known-host-meta">
                  {item.key_type} · {when(item.added_at)}
                </div>
                <div className="known-host-fp" title={item.fingerprint ?? ""}>
                  {item.fingerprint ?? t("knownHosts.invalidFingerprint")}
                </div>
              </div>
              <button
                className="danger"
                disabled={busy}
                onClick={() => void remove(item)}
              >
                {t("knownHosts.delete")}
              </button>
            </div>
          ))}
        </div>
        <div className="modal-actions">
          <button onClick={onClose}>{t("common.close")}</button>
        </div>
      </div>
    </div>
  );
}
