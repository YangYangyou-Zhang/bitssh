import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import { terminalUrl, WS_PROTOCOLS } from "../api/client";
import { useApp, type Tab } from "../state/store";

const FONT =
  '"JetBrains Mono","Cascadia Mono","Sarasa Mono SC","Noto Sans Mono CJK SC",monospace';

export default function TerminalPane({ tab }: { tab: Tab }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const hexRef = useRef(false);
  const tsRef = useRef(false);
  const { t } = useTranslation();

  const [hex, setHex] = useState(false);
  const [timestamps, setTimestamps] = useState(false);
  const [dtr, setDtr] = useState(false);
  const [rts, setRts] = useState(false);
  const [crlf, setCrlf] = useState(true);
  const [sendText, setSendText] = useState("");
  const theme = useApp((s) => s.theme);

  useEffect(() => {
    hexRef.current = hex;
  }, [hex]);
  useEffect(() => {
    tsRef.current = timestamps;
  }, [timestamps]);

  const sendControl = (payload: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  };

  const sendSerial = () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(new TextEncoder().encode(sendText + (crlf ? "\r\n" : "")));
    setSendText("");
  };

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const store = useApp.getState();

    const css = getComputedStyle(document.documentElement);
    const cssVar = (name: string, fallback: string) =>
      css.getPropertyValue(name).trim() || fallback;
    const term = new Terminal({
      fontFamily: FONT,
      fontSize: 14,
      cursorBlink: true,
      scrollback: 5000,
      theme: {
        background: cssVar("--term-bg", "#18211e"),
        foreground: cssVar("--term-fg", "#cccccc"),
        cursor: cssVar("--term-cursor", "#6fa904"),
        selectionBackground: cssVar("--term-sel", "#304802"),
      },
    });
    termRef.current = term;
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(host);

    // xterm.js ships no copy/paste key bindings; terminals conventionally use
    // Ctrl+Shift+C/V (Ctrl/Shift+Insert also works). Prefer the Qt clipboard
    // bridge because Chromium's async clipboard API is permission-sensitive
    // inside QtWebEngine.
    type ClipBridge = {
      setClipboard?: (text: string) => void;
      getClipboard?: (callback: (text: string) => void) => void;
    };
    // On Windows Chromium maps Ctrl+Shift+V to a native paste into xterm's
    // textarea. QtWebEngine on Linux does not, so there we paste through the
    // Qt clipboard bridge for the same shortcut.
    const nativeShiftPaste = navigator.userAgent.includes("Windows");
    const clipBridge = () =>
      (window as unknown as { __bridge?: ClipBridge }).__bridge;

    const copySelection = async () => {
      const text = term.getSelection();
      if (!text) return;
      const bridge = clipBridge();
      if (bridge?.setClipboard) {
        bridge.setClipboard(text);
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch {
        /* fall through to execCommand */
      }
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
      } finally {
        area.remove();
      }
    };

    const pasteClipboard = () => {
      const bridge = clipBridge();
      if (bridge?.getClipboard) {
        bridge.getClipboard((text) => {
          if (text) term.paste(text);
        });
        return;
      }
      navigator.clipboard
        ?.readText()
        .then((text) => {
          if (text) term.paste(text);
        })
        .catch(() => {
          const area = document.createElement("textarea");
          area.style.position = "fixed";
          area.style.opacity = "0";
          document.body.appendChild(area);
          area.focus();
          const ok = document.execCommand("paste");
          const text = area.value;
          area.remove();
          if (ok && text) term.paste(text);
        });
    };

    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown") return true;
      const key = event.key.toLowerCase();
      const block = () => {
        event.preventDefault();
        event.stopPropagation();
        return false;
      };
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && key === "c") {
        void copySelection();
        return block();
      }
      if (event.ctrlKey && event.key === "Insert") {
        void copySelection();
        return block();
      }
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && key === "v") {
        if (nativeShiftPaste) return true;
        pasteClipboard();
        return block();
      }
      if (event.shiftKey && event.key === "Insert") {
        if (nativeShiftPaste) return true;
        pasteClipboard();
        return block();
      }
      return true;
    });

    // WebGL is opt-in: its glyph atlas can garble dense CJK output and a
    // software-only context drops characters, so require hardware WebGL2.
    let webgl: WebglAddon | null = null;
    if ((localStorage.getItem("bitssh.renderer") || "dom") === "webgl") {
      try {
        const probe = document
          .createElement("canvas")
          .getContext("webgl2", { failIfMajorPerformanceCaveat: true });
        if (probe) {
          webgl = new WebglAddon();
          webgl.onContextLoss(() => {
            webgl?.dispose();
            webgl = null;
          });
          term.loadAddon(webgl);
        }
      } catch {
        webgl = null;
      }
    }

    const ws = new WebSocket(terminalUrl(tab.sessionId), WS_PROTOCOLS);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    let unacked = 0;
    let disposed = false;
    let frame = 0;
    let lastW = 0;
    let lastH = 0;
    const encoder = new TextEncoder();

    const send = (payload: unknown) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
    };

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
    });
    term.onWriteParsed(() => {
      if (unacked > 0) {
        send({ type: "ack", bytes: unacked });
        unacked = 0;
      }
    });

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        const type = msg.type as string;
        const asHostkey = () => ({
          sessionId: tab.sessionId,
          host: String(msg.host ?? ""),
          port: Number(msg.port ?? 22),
          key_type: String(msg.key_type ?? ""),
          fingerprint: String(msg.fingerprint ?? ""),
        });
        const asKbdint = () => ({
          sessionId: tab.sessionId,
          name: String(msg.name ?? ""),
          instruction: String(msg.instruction ?? ""),
          prompts: (msg.prompts as [string, boolean][] | undefined) ?? [],
        });

        if (type === "state") {
          store.patchTab(tab.id, { state: String(msg.state) });
          if (msg.hostkey) store.showHostkey(asHostkey());
          if (msg.kbdint) store.showKbdint(asKbdint());
        } else if (type === "hostkey") {
          store.showHostkey(asHostkey());
        } else if (type === "kbdint") {
          store.showKbdint(asKbdint());
        } else if (type === "error") {
          store.patchTab(tab.id, { state: "closed", error: String(msg.code) });
        } else if (type === "exit") {
          store.patchTab(tab.id, { exitCode: Number(msg.code) });
        } else if (type === "ready") {
          store.patchTab(tab.id, { state: "ready", error: null });
        } else if (type === "banner") {
          term.write(`\r\n\x1b[33m${msg.text}\x1b[0m\r\n`);
        }
        return;
      }
      const chunk = new Uint8Array(event.data as ArrayBuffer);
      if (tab.protocol === "serial") {
        if (hexRef.current) {
          const line = Array.from(chunk, (b) => b.toString(16).padStart(2, "0")).join(" ");
          term.write(line + "\r\n");
        } else if (tsRef.current) {
          const now = new Date();
          const stamp = [now.getHours(), now.getMinutes(), now.getSeconds()]
            .map((v) => String(v).padStart(2, "0"))
            .join(":") + "." + String(now.getMilliseconds()).padStart(3, "0");
          term.write(`[${stamp}] `);
          term.write(chunk);
        } else {
          term.write(chunk);
        }
        return;
      }
      unacked += chunk.length;
      term.write(chunk);
    };

    ws.onclose = () => {
      if (!disposed) store.patchTab(tab.id, { state: "closed" });
    };

    const applyFit = () => {
      const rect = host.getBoundingClientRect();
      if (rect.width < 24 || rect.height < 24) return;
      if (rect.width === lastW && rect.height === lastH) return;
      lastW = rect.width;
      lastH = rect.height;
      try {
        fit.fit();
      } catch {
        return;
      }
      send({ type: "resize", cols: term.cols, rows: term.rows });
    };
    const scheduleFit = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(applyFit);
    };
    const observer = new ResizeObserver(scheduleFit);
    observer.observe(host);
    scheduleFit();
    term.focus();

    const onVisibility = () => send({ type: document.hidden ? "pause" : "resume" });
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", onVisibility);
      observer.disconnect();
      cancelAnimationFrame(frame);
      wsRef.current = null;
      ws.close();
      webgl?.dispose();
      term.dispose();
      termRef.current = null;
    };
  }, [tab.id, tab.sessionId]);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    const css = getComputedStyle(document.documentElement);
    const cssVar = (name: string, fallback: string) =>
      css.getPropertyValue(name).trim() || fallback;
    term.options.theme = {
      background: cssVar("--term-bg", "#18211e"),
      foreground: cssVar("--term-fg", "#cccccc"),
      cursor: cssVar("--term-cursor", "#6fa904"),
      selectionBackground: cssVar("--term-sel", "#304802"),
    };
  }, [theme]);

  const overlayKey =
    tab.state === "ready" || tab.state === "idle"
      ? null
      : tab.state === "hostkey_check"
        ? "hostkey"
        : tab.state === "connecting"
          ? "connecting"
          : tab.state === "auth"
            ? "auth"
            : tab.state === "closed"
              ? "closed"
              : null;

  return (
    <div className="pane">
      {tab.protocol === "serial" && (
        <div className="serialbar">
          <button
            className={dtr ? "primary" : ""}
            onClick={() => {
              const next = !dtr;
              setDtr(next);
              sendControl({ type: "serial.line", dtr: next });
            }}
          >
            {t("serial.dtr")}
          </button>
          <button
            className={rts ? "primary" : ""}
            onClick={() => {
              const next = !rts;
              setRts(next);
              sendControl({ type: "serial.line", rts: next });
            }}
          >
            {t("serial.rts")}
          </button>
          <button onClick={() => sendControl({ type: "serial.break" })}>
            {t("serial.break")}
          </button>
          <label className="inline">
            <input type="checkbox" checked={hex} onChange={(e) => setHex(e.target.checked)} />
            {t("serial.hex")}
          </label>
          <label className="inline">
            <input
              type="checkbox"
              checked={timestamps}
              onChange={(e) => setTimestamps(e.target.checked)}
            />
            {t("serial.timestamps")}
          </label>
          <span className="spacer" />
          <label className="inline">
            <input type="checkbox" checked={crlf} onChange={(e) => setCrlf(e.target.checked)} />
            {t("serial.crlf")}
          </label>
          <input
            className="serial-input"
            placeholder={t("serial.placeholder")}
            value={sendText}
            onChange={(e) => setSendText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") sendSerial();
            }}
          />
          <button onClick={sendSerial}>{t("serial.send")}</button>
        </div>
      )}
      <div className="term" ref={hostRef} />
      {tab.error && (
        <div className="pane-error">
          {t(`errors.${tab.error}`, { defaultValue: t("errors.UNKNOWN") })}
        </div>
      )}
      {overlayKey && !tab.error && (
        <div className="pane-overlay">{t(`terminal.${overlayKey}`)}</div>
      )}
    </div>
  );
}
