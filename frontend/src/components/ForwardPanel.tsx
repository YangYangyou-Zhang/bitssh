import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type Forward } from "../api/client";

export default function ForwardPanel({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<Forward[]>([]);
  const [type, setType] = useState<Forward["type"]>("local");
  const [bindHost, setBindHost] = useState("127.0.0.1");
  const [bindPort, setBindPort] = useState(0);
  const [destHost, setDestHost] = useState("127.0.0.1");
  const [destPort, setDestPort] = useState(80);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setItems(await api.forwards());
    } catch {
      /* keep last snapshot */
    }
  }, []);

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => void reload(), 1500);
    return () => window.clearInterval(timer);
  }, [reload]);

  async function start() {
    try {
      await api.createForward({
        session_id: sessionId,
        type,
        bind_host: bindHost,
        bind_port: Number(bindPort) || 0,
        dest_host: type === "socks" ? "" : destHost,
        dest_port: type === "socks" ? 0 : Number(destPort) || 0,
      });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  const mine = items.filter((item) => item.session_id === sessionId);

  return (
    <aside className="sftp">
      <section>
        <header>
          <span className="title">{t("fwd.panel")}</span>
        </header>
        <div className="fwd-form">
          <label>
            {t("fwd.type")}
            <select value={type} onChange={(e) => setType(e.target.value as Forward["type"])}>
              <option value="local">{t("fwd.local")}</option>
              <option value="remote">{t("fwd.remote")}</option>
              <option value="socks">{t("fwd.socks")}</option>
            </select>
          </label>
          <label>
            {t("fwd.bind")}
            <div className="fwd-pair">
              <input value={bindHost} onChange={(e) => setBindHost(e.target.value)} />
              <input
                type="number"
                title={t("fwd.auto")}
                value={bindPort}
                onChange={(e) => setBindPort(Number(e.target.value))}
              />
            </div>
          </label>
          {type !== "socks" && (
            <label>
              {t("fwd.dest")}
              <div className="fwd-pair">
                <input value={destHost} onChange={(e) => setDestHost(e.target.value)} />
                <input
                  type="number"
                  value={destPort}
                  onChange={(e) => setDestPort(Number(e.target.value))}
                />
              </div>
            </label>
          )}
          <button className="primary" onClick={() => void start()}>
            {t("fwd.start")}
          </button>
        </div>
      </section>

      <section className="transfers">
        <header>
          <span className="title">{t("fwd.panel")}</span>
        </header>
        {mine.length === 0 && <div className="empty">{t("fwd.none")}</div>}
        {mine.map((item) => (
          <div key={item.id} className="transfer-row">
            <span className="fs-name">
              {item.type === "socks"
                ? `SOCKS ${item.bind_host}:${item.bind_port}`
                : `${item.bind_host}:${item.bind_port} → ${item.dest_host}:${item.dest_port}`}
            </span>
            <button onClick={() => void api.deleteForward(item.id).then(reload)}>
              {t("fwd.stop")}
            </button>
          </div>
        ))}
      </section>

      {error && (
        <div className="pane-error" onClick={() => setError(null)}>
          {t(`errors.${error}`, { defaultValue: t("errors.UNKNOWN") })}
        </div>
      )}
    </aside>
  );
}
