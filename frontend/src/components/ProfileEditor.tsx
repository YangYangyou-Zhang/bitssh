import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type Profile } from "../api/client";
import { useApp } from "../state/store";

function blank(): Profile {
  return {
    id: crypto.randomUUID().replace(/-/g, ""),
    name: "",
    protocol: "ssh",
    host: "",
    port: 22,
    username: "",
    auth: { method: "password", credential_id: null },
    jump: [],
    forwards: [],
    serial: { port: "", baudrate: 115200, bytesize: 8, parity: "N", stopbits: 1, encoding: "utf-8" },
    terminal: { term: "xterm-256color", scrollback: 5000 },
    tags: [],
    note: "",
  };
}

export default function ProfileEditor({
  profile,
  onClose,
  onSaved,
}: {
  profile: Profile | null;
  onClose: () => void;
  onSaved: (profile: Profile) => void;
}) {
  const { t } = useTranslation();
  const saveProfile = useApp((s) => s.saveProfile);
  const [form, setForm] = useState<Profile>(profile ?? blank());
  const [password, setPassword] = useState("");
  const [keyPath, setKeyPath] = useState(form.auth.key_path ?? "");
  const [saving, setSaving] = useState(false);

  const isSerial = form.protocol === "serial";
  const wantsPassword = !isSerial && (form.auth.method === "password" || form.auth.method === "kbdint");

  type Hop = { host: string; port: number; username: string; auth: { method: string; key_path?: string | null } };
  const jumps = (form.jump as Hop[]) ?? [];

  const setJump = (index: number, patch: Partial<Hop>) => {
    const next = jumps.map((hop, i) => (i === index ? { ...hop, ...patch } : hop));
    set("jump", next as unknown as Profile["jump"]);
  };
  const addJump = () =>
    set("jump", [
      ...jumps,
      { host: "", port: 22, username: "", auth: { method: "key", key_path: "" } },
    ] as unknown as Profile["jump"]);
  const removeJump = (index: number) =>
    set("jump", jumps.filter((_, i) => i !== index) as unknown as Profile["jump"]);

  const set = <K extends keyof Profile>(key: K, value: Profile[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  async function submit() {
    setSaving(true);
    try {
      let credentialId = form.auth.credential_id ?? null;
      if (wantsPassword && password) {
        credentialId = credentialId || crypto.randomUUID().replace(/-/g, "");
        await api.putSecret(credentialId, password);
      }
      const next: Profile = {
        ...form,
        name: form.name.trim() || `${form.username || "user"}@${form.host || "host"}`,
        auth: {
          ...form.auth,
          credential_id: credentialId,
          key_path: form.auth.method === "key" ? keyPath || null : null,
        },
      };
      await saveProfile(next);
      onSaved(next);
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <h2>{profile ? t("profile.edit") : t("profile.new")}</h2>

        <label>
          {t("profile.name")}
          <input value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
        </label>

        <div className="row">
          <label>
            {t("profile.protocol")}
            <select value={form.protocol} onChange={(e) => set("protocol", e.target.value as Profile["protocol"])}>
              <option value="ssh">SSH</option>
              <option value="serial">Serial</option>
            </select>
          </label>
          <label>
            {t("profile.term")}
            <input value={form.terminal.term} onChange={(e) => set("terminal", { ...form.terminal, term: e.target.value })} />
          </label>
        </div>

        {isSerial ? (
          <div className="row">
            <label>
              {t("profile.serial")}
              <input
                value={String((form.serial as { port?: string }).port ?? "")}
                onChange={(e) => set("serial", { ...form.serial, port: e.target.value })}
              />
            </label>
            <label>
              {t("profile.baudrate")}
              <input
                type="number"
                value={Number((form.serial as { baudrate?: number }).baudrate ?? 115200)}
                onChange={(e) => set("serial", { ...form.serial, baudrate: Number(e.target.value) })}
              />
            </label>
          </div>
        ) : (
          <>
            <div className="row">
              <label className="grow">
                {t("profile.host")}
                <input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="192.168.1.1" />
              </label>
              <label className="narrow">
                {t("profile.port")}
                <input type="number" value={form.port} onChange={(e) => set("port", Number(e.target.value))} />
              </label>
            </div>
            <div className="row">
              <label className="grow">
                {t("profile.username")}
                <input value={form.username} onChange={(e) => set("username", e.target.value)} />
              </label>
              <label className="grow">
                {t("profile.auth")}
                <select
                  value={form.auth.method}
                  onChange={(e) =>
                    set("auth", { ...form.auth, method: e.target.value as Profile["auth"]["method"] })
                  }
                >
                  <option value="password">{t("profile.authPassword")}</option>
                  <option value="key">{t("profile.authKey")}</option>
                  <option value="agent">{t("profile.authAgent")}</option>
                  <option value="kbdint">{t("profile.authKbdint")}</option>
                </select>
              </label>
            </div>

            {wantsPassword && (
              <label>
                {t("profile.password")}
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={profile ? t("profile.passwordKeep") : ""}
                />
              </label>
            )}

            {form.auth.method === "key" && (
              <label>
                {t("profile.keyPath")}
                <input value={keyPath} onChange={(e) => setKeyPath(e.target.value)} placeholder="C:\\Users\\me\\.ssh\\id_ed25519" />
              </label>
            )}

            <div className="jumps">
              <div className="jumps-head">
                <span>{t("profile.jump")}</span>
                <button type="button" onClick={addJump}>+ {t("profile.addJump")}</button>
              </div>
              {jumps.map((hop, index) => (
                <div className="row" key={index}>
                  <input
                    placeholder={t("profile.hostPlaceholder")}
                    value={hop.host}
                    onChange={(e) => setJump(index, { host: e.target.value })}
                  />
                  <input
                    type="number"
                    className="narrow"
                    value={hop.port}
                    onChange={(e) => setJump(index, { port: Number(e.target.value) })}
                  />
                  <input
                    placeholder={t("profile.username")}
                    value={hop.username}
                    onChange={(e) => setJump(index, { username: e.target.value })}
                  />
                  <input
                    placeholder={t("profile.keyFile")}
                    value={hop.auth.key_path ?? ""}
                    onChange={(e) =>
                      setJump(index, { auth: { ...hop.auth, method: "key", key_path: e.target.value } })
                    }
                  />
                  <button type="button" onClick={() => removeJump(index)}>×</button>
                </div>
              ))}
            </div>
          </>
        )}

        <label>
          {t("profile.note")}
          <input value={form.note} onChange={(e) => set("note", e.target.value)} />
        </label>

        <div className="modal-actions">
          <button onClick={onClose}>{t("common.cancel")}</button>
          <button className="primary" disabled={saving} onClick={() => void submit()}>
            {t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
