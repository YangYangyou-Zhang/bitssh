import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Profile } from "../api/client";
import { useApp } from "../state/store";

export default function Sidebar({
  onNew,
  onEdit,
}: {
  onNew: () => void;
  onEdit: (profile: Profile) => void;
}) {
  const { t } = useTranslation();
  const profiles = useApp((s) => s.profiles);
  const openSession = useApp((s) => s.openSession);
  const deleteProfile = useApp((s) => s.deleteProfile);
  const [filter, setFilter] = useState("");

  const visible = profiles.filter((p) =>
    p.name.toLowerCase().includes(filter.toLowerCase()) ||
    p.host.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span>{t("app.sessions")}</span>
        <button onClick={onNew}>+ {t("app.new")}</button>
      </div>
      <input
        className="search"
        placeholder={t("app.sessions")}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="profile-list">
        {visible.length === 0 && <div className="empty">{t("app.noSession")}</div>}
        {visible.map((profile) => (
          <div
            key={profile.id}
            className="profile-row"
            onDoubleClick={() => void openSession(profile)}
            title={`${profile.username}@${profile.host}:${profile.port}`}
          >
            <div className="profile-main">
              <div className="profile-name">{profile.name}</div>
              <div className="profile-sub">
                {profile.protocol === "serial"
                  ? String((profile.serial as { port?: string }).port ?? "")
                  : `${profile.username ? profile.username + "@" : ""}${profile.host}:${profile.port}`}
              </div>
            </div>
            <div className="profile-actions">
              <button onClick={() => void openSession(profile)}>{t("common.connect")}</button>
              <button onClick={() => onEdit(profile)}>{t("common.edit")}</button>
              <button
                onClick={() => {
                  void useApp
                    .getState()
                    .askConfirm(`${t("common.delete")} ${profile.name}?`)
                    .then((ok) => {
                      if (ok) void deleteProfile(profile.id);
                    });
                }}
              >
                {t("common.delete")}
              </button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
