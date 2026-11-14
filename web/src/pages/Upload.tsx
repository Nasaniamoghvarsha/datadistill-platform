import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Camera, Copy, FileText, FileUp, Film, Folder, FolderPlus, FolderSearch, Inbox, Link2, MessageSquareText, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import { api, fmtBytes, fmtDate, fmtDuration } from "../api";
import { sha256File } from "../hash";
import { Badge, Button, EmptyState, Modal, Progress, RowMenu, usePoll, useUI } from "../ui";

const kindIcon: any = { video: Film, document: FileText, transcript: MessageSquareText };
const kindLabel: any = { video: "Recording", document: "Document", transcript: "Teams captions", other: "Other" };

export default function Upload() {
  const { confirm, toast } = useUI();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const folders = usePoll(() => api.get("/api/folders"), 3000);
  const list: any[] = folders.data || [];
  const folder = params.get("folder") || list[0]?.name || "";
  const files = usePoll(() => (folder ? api.get(`/api/files?folder=${encodeURIComponent(folder)}`) : Promise.resolve([])), 1500, [folder]);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const select = (name: string) => setParams({ folder: name });

  async function act(p: Promise<any>, msg?: string) {
    try {
      const r = await p;
      if (msg) toast(msg);
      folders.reload();
      files.reload();
      return r;
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }

  async function newFolder() {
    const name = await confirm({ title: "New folder", body: "Keep files that belong together in one folder, for example a meeting with its slides.", confirm: "Create", input: { label: "Folder name", value: "" } });
    if (name && name.trim()) {
      const r = await act(api.post("/api/folders", { name }), `Folder ${name.trim()} created`);
      if (r) select(r.name);
    }
  }
  async function renameFolder(f: any) {
    const name = await confirm({ title: `Rename ${f.name}`, confirm: "Rename", input: { label: "Folder name", value: f.name } });
    if (name && name.trim() && name.trim() !== f.name) {
      const r = await act(api.patch(`/api/folders/${encodeURIComponent(f.name)}`, { new_name: name }), "Folder renamed");
      if (r) select(r.name);
    }
  }
  async function deleteFolder(f: any) {
    let c: any;
    try {
      c = await api.get(`/api/folders/${encodeURIComponent(f.name)}/contents`);
    } catch (e: any) {
      toast(e.message, "bad");
      return;
    }
    if (c.processing) {
      toast(`A file in ${f.name} is being processed. Cancel it or wait until it is done, then delete the folder.`, "bad");
      return;
    }
    if (!c.files) {
      if (await confirm({ title: `Delete ${f.name}?`, body: "The folder is empty.", confirm: "Delete folder", danger: true })) {
        await act(api.del(`/api/folders/${encodeURIComponent(f.name)}`), "Folder deleted");
        setParams({});
      }
      return;
    }
    const what = [
      `${c.files} file${c.files === 1 ? "" : "s"}${c.bytes ? ` (${fmtBytes(c.bytes)} on disk)` : ""}`,
      c.recordings ? `${c.recordings} transcript${c.recordings === 1 ? "" : "s"} with their images and voice samples` : "",
      c.documents ? `${c.documents} document${c.documents === 1 ? "" : "s"} with their slide text` : "",
    ].filter(Boolean).join(", ");
    const typed = await confirm({
      title: `Delete ${f.name} and everything in it?`,
      body: (
        <div className="space-y-2">
          <p>This permanently deletes {what}. It cannot be undone.</p>
          {c.shortcuts_elsewhere > 0 && (
            <p>{c.shortcuts_elsewhere === 1 ? "1 shortcut in another folder points" : `${c.shortcuts_elsewhere} shortcuts in other folders point`} here: that file is moved to the shortcut's place instead of deleted, so the other folder keeps its content.</p>
          )}
        </div>
      ),
      confirm: "Delete folder and files",
      danger: true,
      input: { label: `Type ${f.name} to confirm`, value: "" },
    });
    if (typed === false) return;
    if (typed.trim() !== f.name) {
      toast("The name did not match. Nothing was deleted.", "bad");
      return;
    }
    const r = await act(api.del(`/api/folders/${encodeURIComponent(f.name)}?with_files=true`));
    if (r) {
      const kept = r.kept_as_shortcut_target?.length || 0;
      toast(`${f.name} deleted: ${r.deleted_files} file${r.deleted_files === 1 ? "" : "s"} removed`
        + (r.freed_bytes ? `, ${fmtBytes(r.freed_bytes)} freed` : "")
        + (kept ? `; ${kept} moved to the folder${kept === 1 ? "" : "s"} that had a shortcut to ${kept === 1 ? "it" : "them"}` : ""));
      setParams({});
    }
  }
  async function move(f: any) {
    const others = list.map((x) => x.name).filter((n) => n !== f.folder);
    if (!others.length) return toast("Create another folder first.", "info");
    const to = await confirm({ title: `Move ${f.name}`, confirm: "Move", input: { label: "Folder", value: others[0], options: others } });
    if (to) act(api.post(`/api/files/${f.id}/move`, { folder: to }), `Moved to ${to}`);
  }
  async function remove(f: any) {
    if (await confirm({ title: `Remove ${f.name}?`, body: "It is removed from DataDistill. The uploaded file stays on disk.", confirm: "Remove", danger: true }))
      act(api.del(`/api/files/${f.id}`), `${f.name} removed`);
  }

  const rows: any[] = (files.data || []).slice().sort((a: any, b: any) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "video" ? -1 : 1));

  // re-encoded copies found while processing: ask once per file what to do
  const [dismissed, setDismissed] = useState<number[]>([]);
  const sameRec = rows.filter((f) => f.status === "duplicate" && f.meta?.similar_to && !dismissed.includes(f.id));
  async function resolveSame(f: any, action: "shortcut" | "keep") {
    const r = await act(api.post(`/api/files/${f.id}/same-recording`, { action }),
      action === "shortcut" ? `${f.name} replaced by a shortcut (${fmtBytes(f.size)} freed)` : `${f.name} will be transcribed as a separate recording`);
    if (r) setDismissed((d) => [...d, f.id]);
  }

  return (
    <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
      {/* folders */}
      <div className="panel w-full shrink-0 lg:sticky lg:top-5 lg:w-64">
        <div className="flex items-center justify-between px-5 pb-3 pt-5">
          <h2 className="text-base font-bold">Folders</h2>
          <Button size="sm" variant="ghost" icon={FolderPlus} onClick={newFolder}>New</Button>
        </div>
        <div className="space-y-0.5 px-3 pb-6">
          {!list.length && <p className="px-2 py-3 text-[13px] text-fg-3">Create a folder to start.</p>}
          {list.map((f) => (
            <div
              key={f.name}
              onClick={() => select(f.name)}
              className={`group flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2 ${f.name === folder ? "bg-accent-50" : "hover:bg-subtle"}`}
            >
              <Folder className={`h-[18px] w-[18px] shrink-0 ${f.name === folder ? "text-accent-600" : "text-fg-3"}`} />
              <div className="min-w-0 flex-1">
                <div className={`truncate text-sm font-medium ${f.name === folder ? "text-accent-700" : "text-fg"}`}>{f.name}</div>
                <div className="tnum text-xs text-fg-3">
                  {f.recordings} recording{f.recordings === 1 ? "" : "s"} · {f.documents} document{f.documents === 1 ? "" : "s"}
                  {f.shortcuts ? ` · ${f.shortcuts} shortcut${f.shortcuts === 1 ? "" : "s"}` : ""}
                </div>
              </div>
              <div className="opacity-0 group-hover:opacity-100">
                <RowMenu items={[{ label: "Rename", icon: Pencil, onClick: () => renameFolder(f) }, { label: "Delete", icon: Trash2, danger: true, onClick: () => deleteFolder(f) }]} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* folder content */}
      <div className="min-w-0 flex-1">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-[30px] font-extrabold leading-9 tracking-[-0.02em]">Upload data</h1>
            <p className="mt-1 text-sm text-fg-3">Recordings are converted to a speaker-by-speaker transcript with timestamps. Slides and documents in the same folder are used as context.</p>
          </div>
          <Button icon={FolderSearch} onClick={() => act(api.post("/api/scan"), "Data folder scanned")}>Scan data folder</Button>
        </div>

        {!folder ? (
          <div className="panel"><EmptyState icon={FolderPlus} title="No folders yet" description="Create a folder for each meeting or project." action={<Button variant="primary" icon={FolderPlus} onClick={newFolder}>New folder</Button>} /></div>
        ) : (
          <>
            <Dropzone folder={folder} onDone={(msg) => act(Promise.resolve(), msg)} />
            {sameRec.length > 0 && <SameRecordingDialog files={sameRec} onResolve={resolveSame} onClose={() => setDismissed((d) => [...d, ...sameRec.map((f) => f.id)])} />}
            <section className="panel mt-6 overflow-hidden">
              <header className="flex items-center justify-between border-b border-line px-5 py-4">
                <h2 className="text-base font-bold">{folder}</h2>
                <span className="tnum text-[13px] text-fg-3">{rows.length} file{rows.length === 1 ? "" : "s"} · {fmtBytes(rows.filter(onDisk).reduce((a, f) => a + f.size, 0))} on disk</span>
              </header>
              {!rows.length ? (
                <EmptyState icon={Inbox} title="This folder is empty" description="Upload a recording and, if you have them, the slides that were shown and the Teams transcript." />
              ) : (
                // fixed column widths: a long file or master name used to push the table wider than the panel and the actions column (Retry, Remove) was cut off
                <table className="w-full table-fixed">
                  <thead>
                    <tr>
                      <th className="th w-[34%]">File</th>
                      <th className="th w-[26%]">Status</th>
                      <th className="th">Result</th>
                      <th className="th w-16" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((f) => {
                      const shortcut = f.status === "shortcut";
                      const Icon = shortcut ? Link2 : kindIcon[f.kind] || FileText;
                      const busy = ["queued", "processing"].includes(f.status);
                      const m = f.meta || {};
                      const ready = isReady(f);
                      const deleted = !!m.original_deleted;
                      return (
                        <tr key={f.id} className={ready ? "cursor-pointer hover:bg-subtle" : ""} onClick={() => ready && nav(`/transcripts/${f.id}`)}>
                          <td className="td overflow-hidden">
                            <div className="flex items-center gap-3">
                              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-line bg-subtle">
                                <Icon className="h-4 w-4 text-fg-3" />
                              </div>
                              <div className="min-w-0">
                                <div className="truncate font-medium text-fg">{f.name}</div>
                                <div className="tnum text-xs text-fg-3">
                                  {kindLabel[f.kind]} · {shortcut ? "shortcut, no copy stored" : deleted ? `${fmtBytes(f.size)} original deleted` : fmtBytes(f.size)}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="td overflow-hidden">
                            {busy ? <Running f={f} now={now} /> : <StatusLine f={f} />}
                          </td>
                          <td className="td tnum overflow-hidden text-[13px]">
                            {shortcut && (
                              <div className="space-y-0.5">
                                <div className="truncate text-fg" title={f.master ? `${f.master.folder} / ${f.master.name}` : ""}>
                                  Points to {f.master ? <span className="font-medium">{f.master.name}</span> : "a removed file"}
                                  {f.master && f.master.folder !== f.folder ? <span className="text-fg-3"> in {f.master.folder}</span> : null}
                                </div>
                                <div className="text-fg-3">{fmtBytes(f.size)} not stored again</div>
                              </div>
                            )}
                            {f.status === "duplicate" && f.meta?.similar_to && <SameRecordingNote m={f.meta.similar_to} />}
                            {f.status === "processed" && f.kind === "video" && (
                              <div className="space-y-0.5">
                                <div className="text-fg">{fmtDuration(m.duration)} recording · {m.speakers ?? 0} speaker{m.speakers === 1 ? "" : "s"}</div>
                                <div className="text-fg-3">
                                  Confidence <Conf v={m.confidence} /> · processed in {fmtDuration(m.processing_s)}
                                </div>
                              </div>
                            )}
                            {f.status === "processed" && f.kind === "document" && (
                              <div className="space-y-0.5">
                                <div className="text-fg">{/\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(f.name) ? "image" : `${m.pages} slides`}{m.doc_no ? ` · ${m.doc_no}` : ""}{m.rev ? ` rev ${m.rev}` : ""}</div>
                                <div className="text-fg-3">processed in {fmtDuration(m.processing_s)}</div>
                              </div>
                            )}
                            {f.kind === "transcript" && <span className="text-fg-3">Used for speaker names of the recording with the same name</span>}
                          </td>
                          <td className="td text-right" onClick={(e) => e.stopPropagation()}>
                            <RowMenu
                              items={[
                                ready ? { label: "Open transcript", icon: FileText, onClick: () => nav(`/transcripts/${f.id}`) } : null,
                                f.status === "duplicate" ? { label: "Replace with shortcut", icon: Link2, onClick: () => resolveSame(f, "shortcut") } : null,
                                f.status === "duplicate" ? { label: "Keep as separate recording", icon: RotateCcw, onClick: () => resolveSame(f, "keep") } : null,
                                ["video", "document"].includes(f.kind) && !busy && !shortcut && !deleted && f.status !== "duplicate" ? { label: f.status === "processed" ? "Process again" : f.status === "error" ? "Retry" : "Process", icon: RotateCcw, onClick: () => act(api.post(`/api/files/${f.id}/process`), "Queued") } : null,
                                f.kind === "video" && f.status === "processed" && !m.extracts_at && !deleted ? { label: "Add snapshots and voices", icon: Camera, onClick: () => act(api.post(`/api/recordings/${f.id}/extracts`), "Queued: snapshots and voice samples") } : null,
                                busy && f.job ? { label: "Cancel", icon: X, onClick: () => act(api.post(`/api/jobs/${f.job.id}/cancel`), "Cancelled") } : null,
                                !busy ? { label: "Move to folder", icon: Folder, onClick: () => move(f) } : null,
                                !busy ? { label: "Remove", icon: Trash2, danger: true, onClick: () => remove(f) } : null,
                              ]}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}

export function Conf({ v }: { v?: number | null }) {
  if (v == null) return <span className="text-fg-3">n/a</span>;
  const pct = Math.round(v * 100);
  const c = pct >= 85 ? "text-ok" : pct >= 70 ? "text-fg" : "text-warn";
  return <span className={`font-medium ${c}`}>{pct}%</span>;
}

const onDisk = (f: any) => f.status !== "shortcut" && !f.meta?.original_deleted;
export const isReady = (f: any) =>
  ["video", "document"].includes(f.kind) && (f.status === "processed" || (f.status === "shortcut" && f.master?.status === "processed"));

function SameRecordingNote({ m }: { m: any }) {
  return (
    <div className="space-y-0.5">
      <div className="truncate text-fg" title={`${m.folder} / ${m.name}`}>
        Same meeting as <span className="font-medium">{m.name}</span>
        {m.folder ? <span className="text-fg-3"> in {m.folder}</span> : null}
      </div>
      <div className="text-fg-3">{evidence(m)}</div>
    </div>
  );
}

function evidence(m: any) {
  const sp = m.speech?.length ? Math.round((100 * m.speech.reduce((a: number, b: number) => a + b, 0)) / m.speech.length) : null;
  return `${Math.round(100 * m.frames)}% of the picture matches${sp != null ? ` · ${sp}% of sampled speech matches` : ""}`;
}

function SameRecordingDialog({ files, onResolve, onClose }: { files: any[]; onResolve: (f: any, a: "shortcut" | "keep") => void; onClose: () => void }) {
  return (
    <Modal
      wide
      onClose={onClose}
      title={files.length === 1 ? "This recording is already in DataDistill" : `${files.length} recordings are already in DataDistill`}
      description="Same meeting in a different file (re-encoded or exported again), so the exact-copy check could not see it. The picture and the speech were compared with the existing recording. It was not transcribed again."
      footer={<Button onClick={onClose}>Decide later</Button>}
    >
      <div className="space-y-3">
        {files.map((f) => {
          const m = f.meta.similar_to;
          return (
            <div key={f.id} className="rounded-xl border border-line p-4">
              <div className="flex items-start gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-line bg-subtle">
                  <Copy className="h-4 w-4 text-fg-3" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-fg">{f.name}</div>
                  <div className="mt-0.5 text-[13px] text-fg-3">
                    Same meeting as <span className="font-medium text-fg-2">{m.name}</span> in <span className="font-medium text-fg-2">{m.folder}</span>
                    {m.original_deleted ? " (transcribed, original deleted)" : " (transcribed)"}
                  </div>
                  <div className="mt-0.5 text-xs text-fg-4">{evidence(m)}</div>
                </div>
                <span className="tnum shrink-0 text-xs text-fg-3">{fmtBytes(f.size)}</span>
              </div>
              <div className="mt-3 flex flex-wrap justify-end gap-2">
                <Button size="sm" onClick={() => onResolve(f, "keep")}>Keep as separate recording</Button>
                <Button size="sm" variant="primary" icon={Link2} onClick={() => onResolve(f, "shortcut")}>Replace with shortcut</Button>
              </div>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}

function StatusLine({ f }: { f: any }) {
  if (f.status === "duplicate")
    return (
      <Badge tone="amber" dot title="The same meeting is already in DataDistill as another file">
        Same recording
      </Badge>
    );
  if (f.status === "shortcut")
    return (
      <Badge tone="blue" dot title="Identical to a file already in DataDistill">
        Shortcut
      </Badge>
    );
  if (f.status === "processed" && f.meta?.original_deleted)
    return (
      <div>
        <Badge tone="green" dot>Distilled</Badge>
        <div className="mt-1 text-xs text-fg-3">Original deleted {fmtDate(f.meta.deleted_at)}</div>
      </div>
    );
  if (f.status === "processed") return <Badge tone="green" dot>Done</Badge>;
  if (f.status === "error")
    return (
      <div>
        <Badge tone="red" dot>Failed</Badge>
        <div className="mt-1 line-clamp-2 text-xs text-bad" title={f.error}>{f.error}</div>
      </div>
    );
  if (f.kind === "transcript") return <Badge tone="blue">Attached</Badge>;
  return <Badge>Not processed</Badge>;
}

function Running({ f, now }: { f: any; now: number }) {
  const j = f.job || {};
  const p = j.progress || 0;
  const started = j.started_at;
  const elapsed = started ? now - started : 0;
  const eta = started && p > 0.03 ? (elapsed * (1 - p)) / p : null;
  return (
    <div className="w-full max-w-sm">
      <div className="mb-1.5 flex items-baseline justify-between gap-3 text-xs">
        <span className="truncate font-medium text-fg-2">
          {j.status === "queued" || !started ? (j.position ? (j.position === 1 ? "Next in line" : `Waiting · ${j.position} in line`) : "Waiting in queue") : j.stage || "Starting"}
        </span>
        <span className="tnum font-semibold text-fg">{Math.round(p * 100)}%</span>
      </div>
      <Progress value={p} />
      {started && (
        <div className="tnum mt-1 text-xs text-fg-3">
          {fmtDuration(elapsed)} elapsed{eta != null ? ` · about ${fmtDuration(eta)} left` : ""}
        </div>
      )}
    </div>
  );
}

type Dup = { file: File; dup: any; keep: boolean };

function Dropzone({ folder, onDone }: { folder: string; onDone: (msg: string) => void }) {
  const { toast } = useUI();
  const [phase, setPhase] = useState<{ label: string; p?: number } | null>(null);
  const [review, setReview] = useState<{ fresh: File[]; dups: Dup[] } | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const busy = !!phase;

  async function start(list: FileList | null) {
    const files = Array.from(list || []);
    if (input.current) input.current.value = "";
    if (!files.length) return;
    try {
      // 1. same size as anything in the library? (only those can be identical)  2. SHA-256 in the browser
      const checkable = files.filter((f) => !/\.(vtt|srt)$/i.test(f.name));
      setPhase({ label: "Checking for duplicates" });
      const bySize: any[] = checkable.length ? await api.post("/api/files/check-duplicates", { items: checkable.map((f) => ({ name: f.name, size: f.size })) }) : [];
      const maybe = checkable.filter((_, i) => bySize[i]?.possible);
      const totalBytes = maybe.reduce((a, f) => a + f.size, 0) || 1;
      const hashes: string[] = [];
      let done = 0;
      for (const f of maybe) {
        hashes.push(await sha256File(f, (p) => setPhase({ label: `Checking ${f.name}`, p: (done + p * f.size) / totalBytes })));
        done += f.size;
      }
      const res: any[] = maybe.length ? await api.post("/api/files/check-duplicates", { items: maybe.map((f, i) => ({ name: f.name, size: f.size, sha256: hashes[i] })) }) : [];
      const dups: Dup[] = maybe.map((f, i) => ({ file: f, dup: res[i]?.duplicate, keep: true })).filter((d) => d.dup);
      const fresh = files.filter((f) => !dups.some((d) => d.file === f));
      if (dups.length) {
        setPhase(null);
        setReview({ fresh, dups });
        return;
      }
      await finish(fresh, []);
    } catch (e: any) {
      toast(e.message, "bad");
      setPhase(null);
    }
  }

  async function finish(fresh: File[], shortcuts: Dup[]) {
    setReview(null);
    try {
      let added = 0;
      let serverShortcuts = 0;
      if (fresh.length) {
        setPhase({ label: `Uploading ${fresh.length} file${fresh.length === 1 ? "" : "s"}` });
        const fd = new FormData();
        fd.append("folder", folder);
        fresh.forEach((f) => fd.append("files", f));
        const r = await api.post("/api/files", fd);
        serverShortcuts = r.shortcuts?.length || 0;
        added = r.added.length - serverShortcuts;
        (r.notes || []).forEach((n: string) => toast(n, "info"));
      }
      let saved = 0;
      for (const d of shortcuts) {
        setPhase({ label: `Adding shortcut ${d.file.name}` });
        await api.post("/api/files/shortcut", { folder, name: d.file.name, master_id: d.dup.id });
        saved += d.file.size;
      }
      const sc = shortcuts.length + serverShortcuts;
      const parts = [];
      if (added) parts.push(`${added} file${added === 1 ? "" : "s"} added to ${folder}`);
      if (sc) parts.push(`${sc} shortcut${sc === 1 ? "" : "s"} created${saved ? ` (${fmtBytes(saved)} not stored again)` : ""}`);
      if (parts.length) onDone(parts.join(" · "));
    } catch (e: any) {
      toast(e.message, "bad");
    } finally {
      setPhase(null);
    }
  }

  return (
    <>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          if (!busy) start(e.dataTransfer.files);
        }}
        className={`flex flex-col items-center rounded-xl border border-dashed px-6 py-9 text-center transition ${drag ? "border-accent-500 bg-accent-50" : "border-line-strong bg-surface"}`}
      >
        <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl border border-line bg-subtle">
          <FileUp className="h-5 w-5 text-fg-3" />
        </div>
        {phase ? (
          <div className="w-full max-w-sm">
            <div className="mb-1.5 flex justify-between gap-3 text-xs">
              <span className="truncate font-medium text-fg-2">{phase.label}…</span>
              {phase.p != null && <span className="tnum font-semibold text-fg">{Math.round(phase.p * 100)}%</span>}
            </div>
            <Progress value={phase.p ?? 1} />
          </div>
        ) : (
          <div className="text-sm text-fg-2">
            <button className="font-semibold text-accent-600 hover:text-accent-700" onClick={() => input.current?.click()}>
              Click to upload
            </button>{" "}
            or drag and drop into <span className="font-medium text-fg">{folder}</span>
          </div>
        )}
        <div className="mt-1 text-xs text-fg-3">Recordings (MP4, MOV, MKV, WebM) · Slides and documents (PDF, PPTX) · Teams transcript (.vtt) with the same name as the recording</div>
        <div className="mt-0.5 text-xs text-fg-4">Files already in DataDistill are recognised before uploading and can be added as a shortcut.</div>
        <input ref={input} type="file" multiple className="hidden" accept=".mp4,.m4v,.mov,.mkv,.webm,.avi,.pdf,.pptx,.png,.jpg,.jpeg,.webp,.bmp,.gif,.tif,.tiff,.vtt,.srt" onChange={(e) => start(e.target.files)} />
      </div>
      {review && <DuplicateDialog review={review} setReview={setReview} folder={folder} onCancel={() => setReview(null)} onContinue={() => finish(review.fresh, review.dups.filter((d) => d.keep))} />}
    </>
  );
}

function DuplicateDialog({ review, setReview, folder, onCancel, onContinue }: any) {
  const dups: Dup[] = review.dups;
  const keep = dups.filter((d) => d.keep).length;
  const n = dups.length;
  const set = (i: number, v: boolean) => setReview({ ...review, dups: dups.map((d, j) => (j === i ? { ...d, keep: v } : d)) });
  const label = (st: string, del: boolean) => (st === "processed" ? (del ? "transcribed, original deleted" : "transcribed") : st === "error" ? "processing failed" : "not transcribed yet");
  const nothing = !review.fresh.length && !keep;
  return (
    <Modal
      wide
      onClose={onCancel}
      title={n === 1 ? "This file is already in DataDistill" : `${n} of these files are already in DataDistill`}
      description="Identical content (same SHA-256 fingerprint) was uploaded before. It does not need to be stored or transcribed again."
      footer={
        <>
          <Button onClick={onCancel}>Cancel upload</Button>
          <Button variant="primary" onClick={onContinue} disabled={nothing}>
            {[review.fresh.length ? `Upload ${review.fresh.length} new file${review.fresh.length === 1 ? "" : "s"}` : "", keep ? `Add ${keep} shortcut${keep === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ") || "Continue"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {dups.map((d, i) => (
          <div key={i} className="rounded-xl border border-line p-4">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-line bg-subtle">
                <Copy className="h-4 w-4 text-fg-3" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-fg">{d.file.name}</div>
                <div className="mt-0.5 text-[13px] text-fg-3">
                  Same as <span className="font-medium text-fg-2">{d.dup.name}</span> in <span className="font-medium text-fg-2">{d.dup.folder}</span>, added {fmtDate(d.dup.added_at)} · {label(d.dup.status, d.dup.original_deleted)}
                </div>
                {d.dup.shortcuts?.length > 0 && <div className="mt-0.5 text-xs text-fg-4">Already linked from {Array.from(new Set(d.dup.shortcuts.map((s: any) => s.folder))).join(", ")}</div>}
              </div>
              <span className="tnum shrink-0 text-xs text-fg-3">{fmtBytes(d.file.size)}</span>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <Choice active={d.keep} onClick={() => set(i, true)} title="I still need it here" text={`Add a shortcut in ${folder}. It opens the existing transcript; no second copy is stored.`} />
              <Choice active={!d.keep} onClick={() => set(i, false)} title="Skip this file" text="Nothing is added to this folder." />
            </div>
          </div>
        ))}
      </div>
      {review.fresh.length > 0 && <p className="mt-3 text-[13px] text-fg-3">{review.fresh.length} other file{review.fresh.length === 1 ? " is" : "s are"} new and will be uploaded.</p>}
    </Modal>
  );
}

function Choice({ active, onClick, title, text }: any) {
  return (
    <button onClick={onClick} className={`rounded-xl border px-3 py-2.5 text-left transition ${active ? "border-accent-500 bg-accent-50 ring-1 ring-accent-500" : "border-line hover:bg-subtle"}`}>
      <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
        <span className={`flex h-4 w-4 items-center justify-center rounded-full border ${active ? "border-accent-600" : "border-line-strong"}`}>{active && <span className="h-2 w-2 rounded-full bg-accent-600" />}</span>
        {title}
      </div>
      <div className="mt-1 pl-6 text-xs text-fg-3">{text}</div>
    </button>
  );
}
