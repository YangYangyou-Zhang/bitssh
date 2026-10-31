import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type EditSync, type FsEntry, type Transfer } from "../api/client";
import { useApp } from "../state/store";

const EDIT_LABEL: Record<EditSync["status"], string> = {
  downloading: "editDownloading",
  ready: "editReady",
  uploading: "editUploading",
  synced: "editSynced",
  conflict: "editConflict",
  error: "editError",
};

function joinRemote(base: string, name: string): string {
  return base.endsWith("/") ? base + name : `${base}/${name}`;
}

function joinLocal(base: string, name: string): string {
  return /[\\/]$/.test(base) ? base + name : `${base}/${name}`;
}

function human(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function SftpPanel({
  sessionId,
  profileId,
}: {
  sessionId: string;
  profileId: string;
}) {
  const { t } = useTranslation();
  const remoteKey = `bitssh.sftp.remote.${profileId}`;
  const localKey = `bitssh.sftp.local.${profileId}`;
  const [remotePath, setRemotePath] = useState("/");
  const [remoteDraft, setRemoteDraft] = useState("/");
  const [remote, setRemote] = useState<FsEntry[]>([]);
  const [localPath, setLocalPath] = useState("");
  const [localDraft, setLocalDraft] = useState("");
  const [local, setLocal] = useState<FsEntry[]>([]);
  const [selectedRemote, setSelectedRemote] = useState<string[]>([]);
  const [selectedLocal, setSelectedLocal] = useState<string[]>([]);
  const anchorRemote = useRef<string | null>(null);
  const anchorLocal = useRef<string | null>(null);
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [edits, setEdits] = useState<EditSync[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [dragKind, setDragKind] = useState<"upload" | "download">("upload");
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const handleDrag = useRef(false);
  const internalDropDone = useRef(false);
  const dragNames = useRef<string[]>([]);
  const [activePane, setActivePane] = useState<"remote" | "local">("remote");
  const [showUploadMenu, setShowUploadMenu] = useState(false);

  const loadRemote = useCallback(
    async (path: string) => {
      try {
        const data = await api.sftpList(sessionId, path);
        setRemote(data.entries);
        setRemotePath(data.path);
        setRemoteDraft(data.path);
        setSelectedRemote([]);
        localStorage.setItem(`bitssh.sftp.remote.${profileId}`, data.path);
      } catch (err) {
        setError(err instanceof ApiError ? err.code : "UNKNOWN");
      }
    },
    [sessionId, profileId],
  );

  const loadLocal = useCallback(
    async (path: string) => {
      try {
        const data = await api.localList(path || "~");
        setLocal(data.entries);
        setLocalPath(data.path);
        setLocalDraft(data.path);
        setSelectedLocal([]);
        localStorage.setItem(`bitssh.sftp.local.${profileId}`, data.path);
      } catch (err) {
        setError(err instanceof ApiError ? err.code : "UNKNOWN");
      }
    },
    [profileId],
  );

  useEffect(() => {
    void loadRemote(localStorage.getItem(remoteKey) || "/");
    void loadLocal(localStorage.getItem(localKey) || "~");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    let timer = 0;
    const apply = () => {
      const bridge = (
        window as unknown as { __bridge?: { setContext?: (p: string) => void } }
      ).__bridge;
      if (bridge?.setContext) {
        bridge.setContext(JSON.stringify({ session: sessionId, remote: remotePath }));
        return true;
      }
      return false;
    };
    if (!apply()) {
      timer = window.setInterval(() => {
        if (apply()) window.clearInterval(timer);
      }, 500);
    }
    return () => window.clearInterval(timer);
  }, [sessionId, remotePath]);

  useEffect(() => {
    const host = window as unknown as { __sftpRefresh?: () => void };
    host.__sftpRefresh = () => {
      void loadRemote(remotePath);
      void loadLocal(localPath);
    };
    return () => {
      delete host.__sftpRefresh;
    };
  }, [loadRemote, loadLocal, remotePath, localPath]);

  const goRemoteHome = async () => {
    try {
      const home = await api.sftpHome(sessionId);
      await loadRemote(home.path);
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  };

  useEffect(() => {
    const poll = () => {
      void api.transfers().then(setTransfers).catch(() => undefined);
      void api.edits().then(setEdits).catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, 800);
    return () => window.clearInterval(timer);
  }, []);

  async function waitFor(id: string) {
    for (let i = 0; i < 1200; i += 1) {
      const list = await api.transfers().catch(() => [] as Transfer[]);
      setTransfers(list);
      const item = list.find((x) => x.id === id);
      if (!item || item.status !== "running") return item;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    return undefined;
  }

  const upRemote = () =>
    remotePath === "/" ? "/" : remotePath.replace(/\/[^/]+\/?$/, "") || "/";
  const upLocal = () => {
    const norm = localPath.replace(/\\/g, "/");
    const parent = norm.replace(/\/[^/]+\/?$/, "");
    if (!parent || parent === norm) return;
    void loadLocal(parent);
  };

  async function downloadEntry(entry: FsEntry) {
    if (busy.current) return;
    busy.current = true;
    try {
      // Download into the directory currently shown in the local pane.
      const remote = joinRemote(remotePath, entry.name);
      const dest = joinLocal(localPath, entry.name);
      let job;
      const start = (overwrite: boolean) =>
        entry.dir
          ? api.downloadTree(sessionId, remote, dest, overwrite)
          : api.sftpDownload(sessionId, remote, dest, overwrite);
      try {
        job = await start(false);
      } catch (err) {
        if (err instanceof ApiError && err.code === "LOCAL_FILE_EXISTS") {
          const ok = await useApp
            .getState()
            .askConfirm(t("sftp.overwritePrompt", { name: entry.name }));
          if (!ok) return;
          job = await start(true);
        } else {
          throw err;
        }
      }
      const item = await waitFor(job.id);
      await loadLocal(localPath);
      if (item && item.status === "done") {
        useApp.getState().setNotice(`${t("sftp.savedTo")} ${job.local_path}`);
      } else {
        setError(item?.error || "TRANSFER_FAILED");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    } finally {
      busy.current = false;
    }
  }

  async function uploadEntryOrDir(entry: FsEntry) {
    if (busy.current) return;
    const local = joinLocal(localPath, entry.name);
    busy.current = true;
    try {
      const job = entry.dir
        ? await api.uploadLocal(sessionId, local, joinRemote(remotePath, entry.name))
        : await api.sftpUpload(sessionId, local, joinRemote(remotePath, entry.name));
      const item = await waitFor(job.id);
      await loadRemote(remotePath);
      if (item && item.status === "done") {
        useApp.getState().setNotice(`${t("sftp.uploaded")}: ${entry.name}`);
      } else {
        setError(item?.error || "TRANSFER_FAILED");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    } finally {
      busy.current = false;
    }
  }

  function nativeDragOut(names: string[]) {
    const bridge = (
      window as unknown as { __bridge?: { dragOut?: (payload: string) => void } }
    ).__bridge;
    if (!bridge?.dragOut) {
      useApp.getState().setNotice(t("sftp.dragUnavailable"));
      return;
    }
    const files = remote
      .filter((entry) => names.includes(entry.name))
      .map((entry) => ({
        path: joinRemote(remotePath, entry.name),
        name: entry.name,
        dir: entry.dir,
      }));
    if (files.length === 0) return;
    bridge.dragOut(JSON.stringify({ session: sessionId, files }));
    useApp.getState().setNotice(t("sftp.dragOutOpened"));
  }

  const exportSelected = () => {
    const items = remote
      .filter((entry) => selectedRemote.includes(entry.name))
      .map((entry) => ({
        path: joinRemote(remotePath, entry.name),
        name: entry.name,
        dir: entry.dir,
      }));
    if (items.length === 0) return;
    const query = new URLSearchParams({
      session: sessionId,
      items: JSON.stringify(items),
    });
    window.location.href = `/dialog/export?${query.toString()}`;
  };

  const downloadSelected = async () => {
    const entries = remote.filter((item) => selectedRemote.includes(item.name));
    for (const entry of entries) {
      await downloadEntry(entry);
    }
  };

  const uploadSelected = async () => {
    const entries = local.filter((item) => selectedLocal.includes(item.name));
    for (const entry of entries) {
      await uploadEntryOrDir(entry);
    }
  };

  function toggleSelection(
    entries: FsEntry[],
    name: string,
    event: React.MouseEvent,
    apply: React.Dispatch<React.SetStateAction<string[]>>,
    anchor: React.MutableRefObject<string | null>,
  ) {
    if (event.ctrlKey || event.metaKey) {
      apply((prev) =>
        prev.includes(name) ? prev.filter((item) => item !== name) : [...prev, name],
      );
      anchor.current = name;
      return;
    }
    if (event.shiftKey && anchor.current) {
      const names = entries.map((entry) => entry.name);
      const from = names.indexOf(anchor.current);
      const to = names.indexOf(name);
      if (from >= 0 && to >= 0) {
        const [low, high] = from < to ? [from, to] : [to, from];
        apply(names.slice(low, high + 1));
        return;
      }
    }
    apply([name]);
    anchor.current = name;
  }

  async function removeSelectedRemote() {
    const entries = remote.filter((entry) => selectedRemote.includes(entry.name));
    if (entries.length === 0) return;
    if (!(await useApp.getState().askConfirm(
      t("sftp.confirmDeleteMany", { count: entries.length }),
    ))) {
      return;
    }
    for (const entry of entries) {
      try {
        await api.sftpOps(sessionId, {
          op: "remove",
          path: joinRemote(remotePath, entry.name),
          is_dir: entry.dir,
        });
      } catch {
        /* continue with the rest */
      }
    }
    await loadRemote(remotePath);
  }

  async function removeSelectedLocal() {
    const entries = local.filter((entry) => selectedLocal.includes(entry.name));
    if (entries.length === 0) return;
    if (!(await useApp.getState().askConfirm(
      t("sftp.confirmDeleteMany", { count: entries.length }),
    ))) {
      return;
    }
    for (const entry of entries) {
      try {
        await api.localOps({ op: "remove", path: joinLocal(localPath, entry.name) });
      } catch {
        /* continue with the rest */
      }
    }
    await loadLocal(localPath);
  }

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (activePane === "remote" && selectedRemote.length > 0) {
        event.preventDefault();
        void removeSelectedRemote();
      } else if (activePane === "local" && selectedLocal.length > 0) {
        event.preventDefault();
        void removeSelectedLocal();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePane, selectedRemote, selectedLocal, remote, local, remotePath, localPath]);

  async function removeRemote(entry: FsEntry) {
    const message = entry.dir
      ? t("sftp.deleteDirWarn", { name: entry.name })
      : t("sftp.confirmDelete", { name: entry.name });
    if (!(await useApp.getState().askConfirm(message))) return;
    try {
      await api.sftpOps(sessionId, {
        op: "remove",
        path: joinRemote(remotePath, entry.name),
        is_dir: entry.dir,
      });
      await loadRemote(remotePath);
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  async function removeLocal(entry: FsEntry) {
    const message = entry.dir
      ? t("sftp.deleteDirWarn", { name: entry.name })
      : t("sftp.confirmDelete", { name: entry.name });
    if (!(await useApp.getState().askConfirm(message))) return;
    try {
      await api.localOps({ op: "remove", path: joinLocal(localPath, entry.name) });
      await loadLocal(localPath);
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  async function renameRemote(entry: FsEntry) {
    const name = await useApp
      .getState()
      .askPrompt(t("sftp.renamePrompt", { name: entry.name }), entry.name);
    if (!name || name === entry.name) return;
    try {
      await api.sftpOps(sessionId, {
        op: "rename",
        path: joinRemote(remotePath, entry.name),
        new_path: joinRemote(remotePath, name),
      });
      await loadRemote(remotePath);
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  async function renameLocal(entry: FsEntry) {
    const name = await useApp
      .getState()
      .askPrompt(t("sftp.renamePrompt", { name: entry.name }), entry.name);
    if (!name || name === entry.name) return;
    try {
      await api.localOps({
        op: "rename",
        path: joinLocal(localPath, entry.name),
        new_path: joinLocal(localPath, name),
      });
      await loadLocal(localPath);
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  async function uploadHandle(handle: FileSystemHandle, remoteDir: string): Promise<void> {
    if (handle.kind === "file") {
      const file = await (handle as FileSystemFileHandle).getFile();
      await api.uploadStream(sessionId, joinRemote(remoteDir, handle.name), file);
      return;
    }
    const dir = handle as FileSystemDirectoryHandle;
    const target = joinRemote(remoteDir, dir.name);
    await api
      .sftpOps(sessionId, { op: "mkdir", path: target })
      .catch(() => undefined);
    const iterator = (dir as unknown as { values(): AsyncIterable<FileSystemHandle> }).values();
    for await (const child of iterator) {
      await uploadHandle(child, target);
    }
  }



  const renderList = (
    entries: FsEntry[],
    selected: string[],
    onSelect: (name: string, event: React.MouseEvent) => void,
    onOpen: (entry: FsEntry) => void,
    drag?: { kind: "local" | "remote" },
    actions?: {
      download?: (entry: FsEntry) => void;
      upload?: (entry: FsEntry) => void;
      rename?: (entry: FsEntry) => void;
      remove?: (entry: FsEntry) => void;
    },
  ) => (
    <div className="fs-list">
      {entries.length === 0 && <div className="empty">{t("sftp.empty")}</div>}
      {entries.map((entry) => (
        <div
          key={entry.name}
          className={"fs-row" + (selected.includes(entry.name) ? " selected" : "")}
          onClick={(event) => onSelect(entry.name, event)}
          onDoubleClick={() => onOpen(entry)}
          draggable={Boolean(drag)}
          onDragStart={(event) => {
            if (!drag) return;
            if (handleDrag.current) {
              // Native QDrag already started from the handle; abort the HTML drag
              // so Chromium does not run a competing drag session.
              handleDrag.current = false;
              event.preventDefault();
              return;
            }
            const names = selected.includes(entry.name) ? selected : [entry.name];
            dragNames.current = names;
            internalDropDone.current = false;
            if (drag.kind === "local") {
              event.dataTransfer.setData(
                "application/x-bitssh-local",
                JSON.stringify({ names }),
              );
              event.dataTransfer.setData("text/plain", `bitssh-local:${JSON.stringify(names)}`);
              event.dataTransfer.effectAllowed = "copy";
              return;
            }
            // Internal drag only; native drag-out is started by the ⇱ handle
            // (starting a Qt drag during an HTML drag makes QtWebEngine abort
            // both).
            event.dataTransfer.setData(
              "application/x-bitssh-remote",
              JSON.stringify({ names }),
            );
            event.dataTransfer.setData("text/plain", `bitssh-remote:${JSON.stringify(names)}`);
            event.dataTransfer.effectAllowed = "copy";
          }}
          onDragEnd={() => {
            // Dropped anywhere other than the local pane -> offer drag-out.
            if (drag?.kind !== "remote" || internalDropDone.current) return;
            const names = dragNames.current;
            if (names.length > 0) nativeDragOut(names);
          }}
        >
          <span className="fs-icon">{entry.dir ? "📁" : "📄"}</span>
          {actions?.download && (
            <button
              className="drag-handle"
              title={t("sftp.dragOut")}
              onMouseDown={(event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                event.stopPropagation();
                handleDrag.current = true;
                window.setTimeout(() => {
                  handleDrag.current = false;
                }, 600);
                const names = selected.includes(entry.name) ? selected : [entry.name];
                nativeDragOut(names);
              }}
            >
              ⇱
            </button>
          )}
          <span className="fs-name">{entry.name}</span>
          <span className="fs-size">{entry.dir ? "" : human(entry.size)}</span>
          <span className="fs-actions">
            {actions?.download && (
              <button
                title={t("sftp.download")}
                onClick={(event) => {
                  event.stopPropagation();
                  actions.download?.(entry);
                }}
              >
                ↓
              </button>
            )}
            {actions?.upload && (
              <button
                title={t("sftp.upload")}
                onClick={(event) => {
                  event.stopPropagation();
                  actions.upload?.(entry);
                }}
              >
                ↑
              </button>
            )}
            {actions?.rename && (
              <button
                title={t("common.rename")}
                onClick={(event) => {
                  event.stopPropagation();
                  actions.rename?.(entry);
                }}
              >
                ✎
              </button>
            )}
            {actions?.remove && (
              <button
                title={t("common.delete")}
                onClick={(event) => {
                  event.stopPropagation();
                  actions.remove?.(entry);
                }}
              >
                ×
              </button>
            )}
          </span>
        </div>
      ))}
    </div>
  );

  async function openRemoteEntry(entry: FsEntry) {
    if (entry.dir) {
      void loadRemote(joinRemote(remotePath, entry.name));
      return;
    }
    try {
      await api.editRemote(sessionId, joinRemote(remotePath, entry.name));
      setEdits(await api.edits());
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  async function uploadEntry(entry: FileSystemEntry, remoteDir: string): Promise<void> {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      );
      await api.uploadStream(sessionId, joinRemote(remoteDir, file.name), file);
      return;
    }
    if (entry.isDirectory) {
      const dir = entry as FileSystemDirectoryEntry;
      const target = joinRemote(remoteDir, dir.name);
      await api.sftpOps(sessionId, { op: "mkdir", path: target }).catch(() => undefined);
      const reader = dir.createReader();
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
          reader.readEntries(resolve, reject),
        );
        if (batch.length === 0) break;
        for (const child of batch) await uploadEntry(child, target);
      }
    }
  }

  async function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    event.stopPropagation();
    setDragOver(false);

    // Internal drags: local rows -> upload, remote rows -> download.
    const parseNames = (raw: string): string[] => {
      try {
        const parsed = JSON.parse(raw) as { names?: string[]; name?: string };
        if (Array.isArray(parsed.names)) return parsed.names;
        if (parsed.name) return [parsed.name];
      } catch {
        /* ignore malformed payload */
      }
      return [];
    };
    const plain = event.dataTransfer.getData("text/plain") || "";
    let localNames = parseNames(event.dataTransfer.getData("application/x-bitssh-local"));
    let remoteNames = parseNames(event.dataTransfer.getData("application/x-bitssh-remote"));
    if (localNames.length === 0 && plain.startsWith("bitssh-local:")) {
      localNames = parseNames(plain.slice("bitssh-local:".length));
    }
    if (remoteNames.length === 0 && plain.startsWith("bitssh-remote:")) {
      remoteNames = parseNames(plain.slice("bitssh-remote:".length));
    }
    if (localNames.length > 0) {
      for (const name of localNames) {
        const entry = local.find((item) => item.name === name);
        if (entry) await uploadEntryOrDir(entry);
      }
      return;
    }
    if (remoteNames.length > 0) {
      internalDropDone.current = true;
      for (const name of remoteNames) {
        const entry = remote.find((item) => item.name === name);
        if (entry) await downloadEntry(entry);
      }
      return;
    }

    try {
      // A drag can expose a mix of directory handles, entries and plain files.
      // QtWebEngine sometimes returns a handle for only the first item, so
      // merge every source and de-duplicate by name instead of early-returning.
      const items = Array.from(event.dataTransfer.items as DataTransferItemList);
      const handles: FileSystemHandle[] = [];
      const entries: FileSystemEntry[] = [];
      const files: File[] = [];
      for (const item of items) {
        const getter = (
          item as DataTransferItem & {
            getAsFileSystemHandle?: () => Promise<FileSystemHandle | null>;
          }
        ).getAsFileSystemHandle;
        if (getter) {
          try {
            const handle = await getter.call(item);
            if (handle) handles.push(handle);
          } catch {
            /* fall through */
          }
        }
        const entry = item.webkitGetAsEntry?.();
        if (entry) entries.push(entry);
        const file = item.getAsFile?.();
        if (file) files.push(file);
      }
      for (const file of Array.from(event.dataTransfer.files)) {
        if (!files.some((existing) => existing.name === file.name)) files.push(file);
      }

      const doneNames = new Set<string>();
      let okCount = 0;
      let failCount = 0;
      let firstError = "";
      const attempt = async (name: string, task: () => Promise<void>) => {
        if (doneNames.has(name)) return;
        doneNames.add(name);
        try {
          await task();
          okCount += 1;
        } catch (err) {
          failCount += 1;
          firstError = firstError || (err instanceof Error ? err.message : String(err));
          console.error("upload failed", err);
        }
      };

      for (const handle of handles) {
        await attempt(handle.name, () => uploadHandle(handle, remotePath));
      }
      for (const entry of entries) {
        await attempt(entry.name, () => uploadEntry(entry, remotePath));
      }
      for (const file of files) {
        await attempt(file.name, async () => {
          await api.uploadStream(sessionId, joinRemote(remotePath, file.name), file);
        });
      }

      if (okCount + failCount > 0) {
        await loadRemote(remotePath);
        if (failCount > 0) {
          useApp
            .getState()
            .setNotice(`${t("sftp.uploadFailed")} ×${failCount}: ${firstError}`);
        } else {
          useApp.getState().setNotice(`${t("sftp.uploaded")}: ${okCount}`);
        }
        return;
      }

      useApp.getState().setNotice(t("sftp.folderUnsupported"));
    } catch (err) {
      setError(err instanceof ApiError ? err.code : "UNKNOWN");
    }
  }

  const sectionRefs = useRef<Array<HTMLDivElement | null>>([]);
  const [sectionSizes, setSectionSizes] = useState<number[] | null>(null);

  const startResize = (index: number, event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const current =
      sectionSizes ??
      sectionRefs.current.map((element) => element?.getBoundingClientRect().height ?? 0);
    const startY = event.clientY;
    const minHeight = 70;
    const onMove = (move: PointerEvent) => {
      const delta = move.clientY - startY;
      const next = [...current];
      next[index] = Math.max(minHeight, current[index] + delta);
      next[index + 1] = Math.max(minHeight, current[index + 1] - delta);
      setSectionSizes(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const sectionStyle = (index: number): CSSProperties | undefined =>
    sectionSizes ? { height: sectionSizes[index], flex: "0 0 auto" } : undefined;

  const sectionClass = (extra: string) =>
    "sftp-sec " + extra + (sectionSizes ? " sized" : "");

  return (
    <aside
      className={"sftp" + (dragOver ? " dragover" : "")}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        const types = Array.from(event.dataTransfer.types);
        setDragKind(
          types.includes("application/x-bitssh-remote") ? "download" : "upload",
        );
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(event) => void onDrop(event)}
      onClick={(event) => {
        const target = event.target as HTMLElement;
        if (
          target.closest(".fs-row") ||
          target.closest("header") ||
          target.closest("button") ||
          target.tagName === "INPUT"
        ) {
          return;
        }
        if (!(event.ctrlKey || event.metaKey)) {
          setSelectedRemote([]);
          setSelectedLocal([]);
        }
      }}
    >
      {dragOver && (
        <div className="drop-hint">
          {t(dragKind === "download" ? "sftp.dropDownload" : "sftp.dropHint")}
        </div>
      )}

      <div
        className={sectionClass("remote")}
        style={sectionStyle(0)}
        ref={(element) => {
          sectionRefs.current[0] = element;
        }}
      >
        <header>
          <span className="title">{t("sftp.remote")}</span>
          <input
            className="path-input"
            value={remoteDraft}
            title={remotePath}
            onChange={(e) => setRemoteDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void loadRemote(remoteDraft);
              if (e.key === "Escape") setRemoteDraft(remotePath);
            }}
            onBlur={() => setRemoteDraft(remotePath)}
          />
          <button onClick={() => void loadRemote(upRemote())}>{t("sftp.up")}</button>
          <button onClick={() => void goRemoteHome()}>{t("sftp.home")}</button>
          <button onClick={() => void loadRemote(remotePath)}>{t("sftp.refresh")}</button>
          <button disabled={selectedRemote.length === 0} onClick={() => void downloadSelected()}>
            {t("sftp.download")} ↓
          </button>
          <button className="fs-btn" disabled={selectedRemote.length === 0} onClick={exportSelected}>
            {t("sftp.export")}
          </button>
          <button disabled={selectedRemote.length === 0} onClick={() => void removeSelectedRemote()}>
            {t("sftp.deleteSelected")}
          </button>
        </header>
        {renderList(
          remote,
          selectedRemote,
          (name, event) => {
            setActivePane("remote");
            if (!(event.ctrlKey || event.metaKey || event.shiftKey)) {
              setSelectedLocal([]);
            }
            toggleSelection(remote, name, event, setSelectedRemote, anchorRemote);
          },
          (entry) => void openRemoteEntry(entry),
          { kind: "remote" },
          {
            download: (entry) => void downloadEntry(entry),
            rename: (entry) => void renameRemote(entry),
            remove: (entry) => void removeRemote(entry),
          },
        )}
      </div>

      <div className="splitter" onPointerDown={(e) => startResize(0, e)} />

      <div
        className={sectionClass("local")}
        style={sectionStyle(1)}
        ref={(element) => {
          sectionRefs.current[1] = element;
        }}
      >
        <header>
          <span className="title">{t("sftp.local")}</span>
          <input
            className="path-input"
            value={localDraft}
            title={localPath}
            onChange={(e) => setLocalDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void loadLocal(localDraft);
              if (e.key === "Escape") setLocalDraft(localPath);
            }}
            onBlur={() => setLocalDraft(localPath)}
          />
          <button onClick={upLocal}>{t("sftp.up")}</button>
          <button onClick={() => void loadLocal("~")}>{t("sftp.home")}</button>
          <button onClick={() => void loadLocal(localPath)}>{t("sftp.refresh")}</button>
          <button disabled={selectedLocal.length === 0} onClick={() => void uploadSelected()}>
            {t("sftp.upload")} ↑
          </button>
          <button disabled={selectedLocal.length === 0} onClick={() => void removeSelectedLocal()}>
            {t("sftp.deleteSelected")}
          </button>
          <div className="upload-menu-wrap">
            <button className="fs-btn" onClick={() => setShowUploadMenu((v) => !v)}>
              {t("sftp.uploadMenu")}
            </button>
            {showUploadMenu && (
              <div className="upload-menu" onMouseLeave={() => setShowUploadMenu(false)}>
                <button
                  onClick={() => {
                    setShowUploadMenu(false);
                    const query = new URLSearchParams({
                      session: sessionId,
                      remote: remotePath,
                    });
                    window.location.href = `/dialog/pick-upload-files?${query.toString()}`;
                  }}
                >
                  {t("sftp.uploadFiles")}
                </button>
                <button
                  onClick={() => {
                    setShowUploadMenu(false);
                    const query = new URLSearchParams({
                      session: sessionId,
                      remote: remotePath,
                    });
                    window.location.href = `/dialog/pick-upload-folder?${query.toString()}`;
                  }}
                >
                  {t("sftp.uploadFolder")}
                </button>
              </div>
            )}
          </div>
        </header>
        {renderList(
          local,
          selectedLocal,
          (name, event) => {
            setActivePane("local");
            if (!(event.ctrlKey || event.metaKey || event.shiftKey)) {
              setSelectedRemote([]);
            }
            toggleSelection(local, name, event, setSelectedLocal, anchorLocal);
          },
          (entry) => {
            if (entry.dir) {
              void loadLocal(joinLocal(localPath, entry.name));
            } else {
              void api
                .openPath(joinLocal(localPath, entry.name))
                .catch((err) => setError(err instanceof ApiError ? err.code : "UNKNOWN"));
            }
          },
          { kind: "local" },
          {
            upload: (entry) => void uploadEntryOrDir(entry),
            rename: (entry) => void renameLocal(entry),
            remove: (entry) => void removeLocal(entry),
          },
        )}
      </div>

      <div className="splitter" onPointerDown={(e) => startResize(1, e)} />

      <div
        className={sectionClass("transfers")}
        style={sectionStyle(2)}
        ref={(element) => {
          sectionRefs.current[2] = element;
        }}
      >
        <header>
          <span className="title">{t("sftp.transfers")}</span>
        </header>
        <div className="tr-list">
          {transfers.map((item) => (
            <div key={item.id} className="transfer-row" title={item.target ?? ""}>
              <span className="fs-name">{item.label}</span>
              <span className={"status " + item.status}>{item.status}</span>
              <span className="fs-size">
                {item.total ? `${Math.round((item.copied / item.total) * 100)}%` : ""}
              </span>
              {item.status === "running" && (
                <button onClick={() => void api.cancelTransfer(item.id)}>×</button>
              )}
              {item.kind === "download" && item.status === "done" && item.target && (
                <button onClick={() => void api.openPath(item.target as string)}>
                  {t("sftp.openFolder")}
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="splitter" onPointerDown={(e) => startResize(2, e)} />

      <div
        className={sectionClass("edits")}
        style={sectionStyle(3)}
        ref={(element) => {
          sectionRefs.current[3] = element;
        }}
      >
        <header>
          <span className="title">{t("sftp.edits")}</span>
        </header>
        <div className="tr-list">
          {edits.filter((edit) => edit.session_id === sessionId).length === 0 && (
            <div className="empty">—</div>
          )}
          {edits
            .filter((edit) => edit.session_id === sessionId)
            .map((edit) => (
              <div key={edit.id} className="transfer-row">
                <span className="fs-name" title={edit.remote_path}>
                  {edit.remote_path.split("/").pop()}
                </span>
                <span className={"status " + (edit.status === "error" ? "failed" : edit.status)}>
                  {t(`sftp.${EDIT_LABEL[edit.status]}`)}
                </span>
                {edit.status === "conflict" ? (
                  <>
                    <button
                      className="primary"
                      onClick={() =>
                        void api
                          .resolveEdit(edit.id, "overwrite")
                          .then(async () => setEdits(await api.edits()))
                      }
                    >
                      {t("sftp.resolveOverwrite")}
                    </button>
                    <button
                      onClick={() =>
                        void api
                          .resolveEdit(edit.id, "reload")
                          .then(async () => setEdits(await api.edits()))
                      }
                    >
                      {t("sftp.resolveReload")}
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() =>
                      void api.uploadEdit(edit.id).then(async () => setEdits(await api.edits()))
                    }
                  >
                    {t("sftp.uploadNow")}
                  </button>
                )}
                <button
                  onClick={() =>
                    void api.stopEdit(edit.id).then(async () => setEdits(await api.edits()))
                  }
                >
                  ×
                </button>
              </div>
            ))}
        </div>
      </div>

      {error && (
        <div className="pane-error" onClick={() => setError(null)}>
          {t(`errors.${error}`, { defaultValue: t("errors.UNKNOWN") })}
        </div>
      )}
    </aside>
  );
}
