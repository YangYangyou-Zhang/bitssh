import React from "react";
import ReactDOM from "react-dom/client";
import "@xterm/xterm/css/xterm.css";
import "./i18n";
import App from "./App";
import "./styles.css";
import "./theme-overrides.css";

// The shell injects qwebchannel.js at document creation; once the transport is
// available, expose the native drag bridge for remote rows.
const qtTransport = (window as unknown as { qt?: { webChannelTransport?: unknown } }).qt
  ?.webChannelTransport;
const WebChannelCtor = (window as unknown as { QWebChannel?: new (t: unknown, cb: (c: { objects: { bridge?: unknown } }) => void) => void })
  .QWebChannel;
if (qtTransport && WebChannelCtor) {
  new WebChannelCtor(qtTransport, (channel) => {
    (window as unknown as { __bridge?: unknown }).__bridge = channel.objects.bridge;
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
