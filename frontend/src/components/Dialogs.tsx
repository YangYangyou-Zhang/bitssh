import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useApp } from "../state/store";

export function HostKeyDialog() {
  const { t } = useTranslation();
  const prompt = useApp((s) => s.hostkey);
  const resolve = useApp((s) => s.resolveHostkey);
  if (!prompt) return null;
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>{t("hostkey.title")}</h2>
        <p>{t("hostkey.body", { host: prompt.host, port: prompt.port })}</p>
        <div className="fingerprint">
          <div>
            <span>{t("hostkey.keyType")}</span>
            <code>{prompt.key_type}</code>
          </div>
          <div>
            <span>{t("hostkey.fingerprint")}</span>
            <code>{prompt.fingerprint}</code>
          </div>
        </div>
        <div className="modal-actions">
          <button className="danger" onClick={() => void resolve("reject")}>
            {t("hostkey.reject")}
          </button>
          <button onClick={() => void resolve("once")}>{t("hostkey.once")}</button>
          <button className="primary" onClick={() => void resolve("always")}>
            {t("hostkey.always")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function KbdintDialog() {
  const { t } = useTranslation();
  const prompt = useApp((s) => s.kbdint);
  const resolve = useApp((s) => s.resolveKbdint);
  const [values, setValues] = useState<string[]>([]);

  useEffect(() => {
    setValues(prompt ? prompt.prompts.map(() => "") : []);
  }, [prompt]);

  if (!prompt) return null;
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>{prompt.name || t("kbdint.title")}</h2>
        {prompt.instruction && <p className="mono">{prompt.instruction}</p>}
        {prompt.prompts.map(([text, echo], index) => (
          <label key={index}>
            {text}
            <input
              autoFocus={index === 0}
              type={echo ? "text" : "password"}
              value={values[index] ?? ""}
              onChange={(e) => {
                const next = [...values];
                next[index] = e.target.value;
                setValues(next);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") void resolve(values);
              }}
            />
          </label>
        ))}
        <div className="modal-actions">
          <button onClick={() => void resolve([])}>{t("common.cancel")}</button>
          <button className="primary" onClick={() => void resolve(values)}>
            {t("kbdint.submit")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PasswordDialog() {
  const { t } = useTranslation();
  const profile = useApp((s) => s.passwordFor);
  const submit = useApp((s) => s.submitPassword);
  const [value, setValue] = useState("");

  useEffect(() => setValue(""), [profile]);
  if (!profile) return null;
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>{t("password.title")}</h2>
        <p>{t("password.body", { name: profile.name })}</p>
        <input
          autoFocus
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit(value);
            if (e.key === "Escape") submit(null);
          }}
        />
        <div className="modal-actions">
          <button onClick={() => submit(null)}>{t("common.cancel")}</button>
          <button className="primary" onClick={() => submit(value)}>
            {t("common.connect")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ConfirmDialog() {
  const { t } = useTranslation();
  const message = useApp((s) => s.confirmMessage);
  const submit = useApp((s) => s.submitConfirm);
  const [choice, setChoice] = useState<"yes" | "no">("yes");
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setChoice("yes");
  }, [message]);

  useEffect(() => {
    if (message) boxRef.current?.focus();
  }, [message]);

  if (!message) return null;

  const finish = (ok: boolean) => {
    setChoice("yes");
    submit(ok);
  };

  return (
    <div className="modal-backdrop">
      <div
        className="modal"
        tabIndex={0}
        ref={boxRef}
        onKeyDown={(event) => {
          if (
            event.key === "ArrowLeft" ||
            event.key === "ArrowRight" ||
            event.key === "ArrowUp" ||
            event.key === "ArrowDown"
          ) {
            event.preventDefault();
            setChoice((current) => (current === "yes" ? "no" : "yes"));
          } else if (event.key === "Enter") {
            event.preventDefault();
            finish(choice === "yes");
          } else if (event.key === "Escape") {
            event.preventDefault();
            finish(false);
          }
        }}
      >
        <p style={{ whiteSpace: "pre-wrap" }}>{message}</p>
        <div className="modal-actions">
          <button
            className={choice === "no" ? "primary" : ""}
            onMouseEnter={() => setChoice("no")}
            onClick={() => finish(false)}
          >
            {t("common.no")}
          </button>
          <button
            className={choice === "yes" ? "primary" : ""}
            onMouseEnter={() => setChoice("yes")}
            onClick={() => finish(true)}
          >
            {t("common.yes")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PromptDialog() {
  const { t } = useTranslation();
  const message = useApp((s) => s.promptMessage);
  const initial = useApp((s) => s.promptValue);
  const submit = useApp((s) => s.submitPrompt);
  const [value, setValue] = useState(initial);

  useEffect(() => setValue(initial), [initial]);

  if (!message) return null;
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>{message}</h2>
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit(value);
            if (e.key === "Escape") submit(null);
          }}
        />
        <div className="modal-actions">
          <button onClick={() => submit(null)}>{t("common.cancel")}</button>
          <button className="primary" onClick={() => submit(value)}>
            {t("common.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function NoticeToast() {
  const notice = useApp((s) => s.notice);
  const setNotice = useApp((s) => s.setNotice);
  if (!notice) return null;
  return (
    <div className="toast info" onClick={() => setNotice(null)}>
      {notice}
    </div>
  );
}

export function ErrorToast() {
  const { t } = useTranslation();
  const error = useApp((s) => s.error);
  const setError = useApp((s) => s.setError);
  if (!error) return null;
  return (
    <div className="toast" onClick={() => setError(null)}>
      {t(`errors.${error}`, { defaultValue: error })}
    </div>
  );
}
