import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type Theme } from "../api/client";
import { useApp } from "../state/store";

const GROUPS: { title: string; keys: string[] }[] = [
  { title: "appearance.group.base", keys: ["bg", "bgElev", "bgPanel", "bgHover", "border", "borderStrong"] },
  { title: "appearance.group.text", keys: ["text", "textStrong", "textDim", "textFaint"] },
  { title: "appearance.group.accent", keys: ["accent", "accentHover", "accentBg", "selection", "ok", "warn", "err"] },
  { title: "appearance.group.button", keys: ["buttonBg", "buttonHover", "buttonBorder"] },
  { title: "appearance.group.status", keys: ["statusBg", "statusFg", "errorBg", "errorFg"] },
  { title: "appearance.group.term", keys: ["termBg", "termFg", "termCursor", "termSel"] },
  { title: "appearance.group.window", keys: ["opacity", "selectionText", "focusRing"] },
];

export default function AppearanceDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const current = useApp((s) => s.theme);
  const saveTheme = useApp((s) => s.saveTheme);
  const resetTheme = useApp((s) => s.resetTheme);
  const previewTheme = useApp((s) => s.previewTheme);

  const [draft, setDraft] = useState<Theme>(current);
  const [defaults, setDefaults] = useState<Theme>({});
  const [path, setPath] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void api
      .getTheme()
      .then((data) => {
        setDraft(data.theme);
        setDefaults(data.defaults ?? {});
        setPath(data.path);
      })
      .catch(() => undefined);
  }, []);

  const update = (key: string, value: string) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    const valid =
      key === "opacity"
        ? Number(value) >= 0.2 && Number(value) <= 1
        : /^#[0-9a-fA-F]{6}$/.test(value);
    if (valid) {
      previewTheme({ ...draft, [key]: value });
      setError("");
    } else {
      setError(t("appearance.invalidColor"));
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal appearance" onMouseDown={(e) => e.stopPropagation()}>
        <h2>{t("appearance.title")}</h2>
        <p className="appearance-hint">{t("appearance.hint", { path })}</p>
        <div className="appearance-body">
          {GROUPS.map((group) => (
            <section key={group.title}>
              <h3>{t(group.title)}</h3>
              {group.keys.map((key) =>
                key === "opacity" ? (
                  <div className="color-row" key={key}>
                    <label>{t("theme.opacity")}</label>
                    <input
                      type="range"
                      min={0.2}
                      max={1}
                      step={0.05}
                      value={Number(draft.opacity) || 1}
                      onChange={(e) => update("opacity", Number(e.target.value).toFixed(2))}
                    />
                    <span className="color-text">
                      {Math.round((Number(draft.opacity) || 1) * 100)}%
                    </span>
                  </div>
                ) : (
                  <div className="color-row" key={key}>
                    <label>{t(`theme.${key}`)}</label>
                    <input
                      type="color"
                      value={draft[key] || "#000000"}
                      onChange={(e) => update(key, e.target.value)}
                    />
                    <input
                      className="color-text"
                      value={draft[key] || ""}
                      onChange={(e) => update(key, e.target.value.trim())}
                    />
                  </div>
                ),
              )}
            </section>
          ))}
        </div>
        {error && <div className="pane-error">{error}</div>}
        <div className="modal-actions">
          <button
            onClick={async () => {
              await resetTheme();
              setDraft(defaults);
            }}
          >
            {t("appearance.reset")}
          </button>
          <button
            onClick={() => {
              previewTheme(current);
              onClose();
            }}
          >
            {t("common.cancel")}
          </button>
          <button
            className="primary"
            onClick={async () => {
              try {
                await saveTheme(draft);
                onClose();
              } catch {
                setError(t("appearance.saveFailed"));
              }
            }}
          >
            {t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
