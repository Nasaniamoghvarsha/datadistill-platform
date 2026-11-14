import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Archive, Camera, ScrollText, RotateCw, ChevronDown, ChevronRight, Download, FileJson, FileText, Film, Folder, Link2, Loader2, MessageSquare, MonitorUp, Pause, Pencil, Play, Search as SearchIcon, X } from "lucide-react";
import { api, fmtBytes, fmtClock, fmtDate, fmtDuration } from "../api";
import { Badge, Button, DownloadButton, EmptyState, ErrorNote, Loading, Panel, usePoll, useUI } from "../ui";
import { Conf, isReady } from "./Upload";

const SPEAKER_COLORS = ["#2453e6", "#c4320a", "#067647", "#6941c6", "#c11574", "#0e7090", "#b54708", "#3e4784", "#4e5ba6", "#9e165f"];
const colorOf = (sid: string | null) => (sid ? SPEAKER_COLORS[(parseInt(sid.slice(1)) - 1) % SPEAKER_COLORS.length] : "#98a2b3");

export default function Transcripts() {
  const { id } = useParams();
  const files = usePoll(() => api.get("/api/files"), 3000);
  const all: any[] = (files.data || []).filter((f: any) => ["video", "document"].includes(f.kind));
  const byFolder = useMemo(() => {
    const m: Record<string, any[]> = {};
    all.forEach((f) => (m[f.folder] ||= []).push(f));
    Object.values(m).forEach((l) => l.sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "video" ? -1 : 1)));
    return m;
  }, [files.data]);
  const current = all.find((f) => String(f.id) === id);

  return (
    <div className="flex items-start gap-5">
      <FolderTree byFolder={byFolder} currentId={id} currentFolder={current?.folder} />
      <div className={`${id ? "" : "hidden lg:block"} min-w-0 flex-1`}>
        {id && (
          <Link to="/transcripts" className="mb-3 inline-flex text-[13px] font-medium text-fg-3 hover:text-fg-2 lg:hidden">
            ← All files
          </Link>
        )}
        {!id ? (
          <div className="panel">
            <EmptyState icon={FileText} title="Select a file" description="Choose a recording or document from the folders on the left." />
          </div>
        ) : !current ? (
          files.data ? <ErrorNote error="File not found." /> : <Loading />
        ) : current.status === "duplicate" && current.meta?.similar_to ? (
          <div className="panel">
            <EmptyState icon={Link2} title="Same recording as one already transcribed" description={`This file is the same meeting as ${current.meta.similar_to.name} in ${current.meta.similar_to.folder}, so it was not transcribed again. Choose on the Upload data page whether to replace it with a shortcut or keep it separately.`} action={<Link className="link text-sm" to={`/transcripts/${current.meta.similar_to.id}`}>Open the existing transcript</Link>} />
          </div>
        ) : !isReady(current) ? (
          <div className="panel">
            <EmptyState icon={Loader2} title={current.status === "error" ? "Processing failed" : "Not transcribed yet"} description={current.error || "Follow the progress on the Upload data page."} action={<Link className="link text-sm" to={`/upload?folder=${encodeURIComponent(current.folder)}`}>Go to Upload data</Link>} />
          </div>
        ) : current.kind === "video" ? (
          <RecordingView id={id} reloadFiles={files.reload} />
        ) : (
          <DocumentView id={id} />
        )}
      </div>
    </div>
  );
}

function FolderTree({ byFolder, currentId, currentFolder }: any) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const names = Object.keys(byFolder).sort((a, b) => a.localeCompare(b));
  return (
    <div className={`${currentId ? "hidden lg:block" : "w-full lg:w-64"} panel shrink-0 overflow-y-auto lg:sticky lg:top-5 lg:max-h-[calc(100vh-180px)] lg:w-64`}>
      <h2 className="px-5 pb-3 pt-5 text-base font-bold">Folders</h2>
      {!names.length && <p className="px-5 text-[13px] text-fg-3">Nothing uploaded yet.</p>}
      <div className="px-3 pb-6">
        {names.map((n) => {
          const open = !closed[n] || n === currentFolder;
          return (
            <div key={n} className="mb-1">
              <div className="group flex items-center gap-1 rounded-xl pr-1 hover:bg-subtle">
                <button onClick={() => setClosed({ ...closed, [n]: open })} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left">
                  {open ? <ChevronDown className="h-4 w-4 text-fg-4" /> : <ChevronRight className="h-4 w-4 text-fg-4" />}
                  <Folder className="h-4 w-4 shrink-0 text-fg-3" />
                  <span className="truncate text-sm font-medium text-fg">{n}</span>
                </button>
                <DownloadButton compact href={`/api/folders/${encodeURIComponent(n)}/export`} title="Download all transcripts of this folder (JSON)" cls="rounded-md p-1.5 text-fg-4 opacity-0 hover:bg-line hover:text-fg-2 group-hover:opacity-100 focus-visible:opacity-100">
                  <Download className="h-4 w-4" />
                </DownloadButton>
              </div>
              {open && (
                <div className="ml-6 border-l border-line pl-2">
                  {byFolder[n].map((f: any) => {
                    const Icon = f.status === "shortcut" ? Link2 : f.kind === "video" ? Film : FileText;
                    const busy = ["queued", "processing"].includes(f.status);
                    const active = String(f.id) === currentId;
                    return (
                      <Link
                        key={f.id}
                        to={`/transcripts/${f.id}`}
                        className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] ${active ? "bg-accent-50 font-medium text-accent-700" : "text-fg-2 hover:bg-subtle"}`}
                      >
                        {busy ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent-600" /> : <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? "text-accent-600" : "text-fg-4"}`} />}
                        <span className="truncate">{f.name}</span>
                        {busy && f.job && <span className="tnum ml-auto text-xs text-fg-3">{Math.round((f.job.progress || 0) * 100)}%</span>}
                        {f.status === "error" && <span className="ml-auto text-xs text-bad">failed</span>}
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ================================================================== recording */
/** Lower case, accents removed, every run of punctuation / whitespace one space: text copied from a slide (with its line
 *  breaks and bullets) then still matches the same text in the transcript or on the slide. */
const foldText = (x: string) =>
  (x || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/** What makes two moments "the same slide": its stored id (a slide shown again later is the same slide), else the screen. */
const screenKey = (s: any) => (!s || s.kind === "non_slide" ? null : s.slide_uid || `screen${s.idx}`);

/** Does the typed / pasted text occur in this slide (title and text)? Exact phrase, or - for a longer paste that was copied
 *  with different line breaks or a missed word - at least 80% of its words. */
function slideMatches(s: any, nq: string) {
  if (!s || s.kind === "non_slide" || nq.length < 3) return false;
  const hay = foldText(`${s.title || ""} ${s.text || ""}`);
  if (!hay) return false;
  if (hay.includes(nq)) return true;
  const words = nq.split(" ").filter((w) => w.length > 2);
  if (words.length < 4) return false;
  const have = new Set(hay.split(" "));
  return words.filter((w) => have.has(w)).length / words.length >= 0.8;
}

function RecordingView({ id, reloadFiles }: { id: string; reloadFiles: () => void }) {
  const { toast } = useUI();
  const [zoom, setZoom] = useState<any>(null);
  const [params] = useSearchParams();
  const rec = usePoll(() => api.get(`/api/recordings/${id}`), 0, [id]);
  const [filter, setFilter] = useState("");
  const [showScreens, setShowScreens] = useState(true);
  const d = rec.data;
  const focus = params.get("seg");

  useEffect(() => {
    if (d && focus) setTimeout(() => document.getElementById(`seg-${focus}`)?.scrollIntoView({ block: "center" }), 50);
  }, [d, focus]);

  if (rec.error) return <ErrorNote error={rec.error} />;
  if (!d) return <Loading />;
  const m = d.manifest;
  const q = m.quality;
  const screens: Record<number, any> = Object.fromEntries(d.screens.map((s: any) => [s.idx, s]));
  const snapsBySeg: Record<number, any[]> = {};
  (d.snapshots || []).forEach((s: any) => (snapsBySeg[s.seg_idx] ||= []).push(s));
  const fileUrl = (rel: string) => `/api/recordings/${id}/files/${rel}`;
  const st = d.storage || {};
  const meta = d.file.meta || {};

  async function addExtracts() {
    try {
      await api.post(`/api/recordings/${d.file.id}/extracts`);
      toast("Creating snapshots and voice samples. This takes about a minute.", "info");
      setTimeout(() => { rec.reload(); reloadFiles(); }, 60000);
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }
  const f = filter.toLowerCase();
  const nq = foldText(filter);

  async function saveLine(segId: number, text: string) {
    try {
      await api.patch(`/api/segments/${segId}`, { text });
      toast("Line corrected");
      rec.reload();
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }

  // dialogue turns: consecutive lines of one speaker (pause < 3 s, same shared screen) under one header
  // A search also looks in the text of the shared slides: a line said while a matching slide was up is a hit too, and the
  // slide is shown above it (so text copied from a slide is found).
  const slideHit: Record<number, boolean> = {};
  if (nq) for (const sc of d.screens) slideHit[sc.idx] = slideMatches(sc, nq);
  const blocks: any[] = [];
  for (const t of d.turns) {
    if (nq) {
      const own = foldText(t.text).includes(nq) || foldText(t.speaker_name).includes(nq) || t.text.toLowerCase().includes(f);
      if (!own && !slideHit[t.slide_idx]) continue;
    }
    const b = blocks[blocks.length - 1];
    if (b && b.speaker === t.speaker && b.key === screenKey(screens[t.slide_idx]) && t.start - b.end < 3) {
      b.lines.push(t);
      b.end = t.end;
    } else blocks.push({ speaker: t.speaker, speaker_name: t.speaker_name, slide_idx: t.slide_idx, key: screenKey(screens[t.slide_idx]), start: t.start, end: t.end, lines: [t] });
  }
  let lastScreen: string | null | undefined = undefined;
  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-1.5 text-[13px] text-fg-3"><Folder className="h-3.5 w-3.5" /> {m.source.folder}</div>
          <h1 className="truncate text-[30px] font-extrabold leading-9 tracking-[-0.02em]">{d.shortcut ? d.shortcut.name : m.source.name}</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon={FileText} href={`/api/export/${id}?format=txt`} title="The transcript with the text of every slide where it changes (the slide pictures are in Download all)">Download text</Button>
          <Button icon={FileText} href={`/api/export/${id}?format=ascii`} title="The same text as plain ASCII: accents and Nordic letters are written without them (Hägglund -> Hagglund), quotes and dashes in their simple form">Download ASCII</Button>
          <Button icon={Archive} href={`/api/export/${id}?format=zip`} title="One zip: the PDF report, the transcript with the slides (md, txt, ASCII), the transcript sorted by slide, JSON, the slide pictures, the audio and the voice samples">Download all</Button>
          <Button icon={ScrollText} href={`/api/report/${id}`} title="PDF report: summary, people, quality, storage, screens, transcript, proof and JSON">Report (PDF)</Button>
          <Button variant="primary" icon={FileJson} href={`/api/export/${id}?format=json`}>Download JSON</Button>
        </div>
      </div>

      {d.shortcut && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-accent-100 bg-accent-50 px-4 py-3 text-sm">
          <Link2 className="mt-0.5 h-4 w-4 shrink-0 text-accent-600" />
          <div className="text-fg-2">
            <span className="font-medium text-fg">Shortcut.</span> This file is identical to{" "}
            <Link className="link" to={`/transcripts/${d.file.id}`}>{d.file.name}</Link> in {d.file.folder}, so its transcript is shown here. No second copy is stored.
          </div>
        </div>
      )}
      {meta.original_deleted && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-line bg-subtle px-4 py-3 text-sm">
          <Archive className="mt-0.5 h-4 w-4 shrink-0 text-fg-3" />
          <div className="text-fg-2">
            <span className="font-medium text-fg">Original video deleted</span> {fmtDate(meta.deleted_at)} after distillation ({fmtBytes(meta.original_bytes)}). Kept: this transcript
            {d.snapshots?.length ? `, ${d.snapshots.length} snapshot${d.snapshots.length === 1 ? "" : "s"}` : ""}
            {d.speakers.some((s: any) => s.voice) ? ", voice samples" : ""} · {fmtBytes(st.total)} in total.
          </div>
        </div>
      )}

      <div className="mb-6 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Stat label="Duration" value={fmtDuration(m.source.duration_s)} />
        <Stat label="Recognition confidence" value={<Conf v={q.mean_confidence} />} hint={q.low_confidence_share != null ? `${Math.round(q.low_confidence_share * 100)}% of speech below 60%` : m.transcript.source} />
        <Stat label="Speakers" value={d.speakers.length} hint={`${m.counts.named_speakers} named · ${m.counts.participants_on_screen} names seen on screen`} />
        <Stat label="Processing time" value={fmtDuration(m.timings_s.total)} hint={`Transcribed ${fmtDate(m.processed_at)}`} />
      </div>

      <SummaryPanel id={id} turns={d.turns} />
      <AskPanel id={id} turns={d.turns} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <section className="panel min-w-0">
          <header className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
            <h2 className="text-base font-bold">Transcript</h2>
            <span className="text-xs text-fg-3">{d.turns.length} turns · {m.transcript.source === "whisper" ? `speech recognition (${m.transcript.language || "auto"})` : "Teams captions"}</span>
            <div className="relative ml-auto">
              <SearchIcon className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-fg-4" />
              <input className="field w-64 pl-9" placeholder="Find in transcript or on the slides" value={filter} onChange={(e) => setFilter(e.target.value)} />
            </div>
            {d.screens.some((s: any) => s.kind !== "non_slide") && (
              <label className="flex items-center gap-2 text-[13px] text-fg-3">
                <input type="checkbox" className="h-4 w-4 accent-accent-500" checked={showScreens} onChange={(e) => setShowScreens(e.target.checked)} /> Shared screens
              </label>
            )}
          </header>
          <div className="divide-y divide-line">
            {!d.turns.length && <div className="p-8 text-center text-sm text-fg-3">No speech found in this recording.</div>}
            {blocks.map((b: any) => {
              const s = screens[b.slide_idx];
              // the slide is shown when it CHANGES (or, in a search, when it is the slide that matched); while the same slide
              // stays up, the lines that follow are not preceded by it again
              const showS = showScreens && b.key !== lastScreen && !!b.key && (!nq || slideHit[b.slide_idx]);
              lastScreen = b.key;
              return (
                <div key={b.lines[0].id}>
                  {showS && <ScreenCard s={s} fileUrl={fileUrl} onZoom={setZoom} />}
                  <Block b={b} focus={focus} onSave={saveLine} snaps={b.lines.flatMap((l: any) => snapsBySeg[l.idx] || [])} fileUrl={fileUrl}
                    onZoom={(z: any) => setZoom({ src: fileUrl(z.image), caption: `${fmtClock(z.t)} · “${z.phrase}” · ${z.width}×${z.height}, ${fmtBytes(z.bytes)}` })} />
                </div>
              );
            })}
          </div>
        </section>

        <div className="space-y-6">
          <Speakers id={id} d={d} reload={rec.reload} fileUrl={fileUrl} />
          <Panel title="Stored" description="What is kept of this recording" bodyClass="px-5 py-2">
            <dl className="tnum divide-y divide-line text-[13px]">
              <Row k="Original video" v={st.original_on_disk ? fmtBytes(st.original) : <span className="text-fg-3">deleted ({fmtBytes(st.original)})</span>} />
              <Row k="Transcript (JSON + text)" v={fmtBytes(st.transcript)} />
              <Row k={`Snapshots (${d.snapshots?.length || 0})`} v={fmtBytes(st.snapshots)} />
              <Row k={`Voice samples (${d.speakers.filter((s: any) => s.voice).length})`} v={fmtBytes(st.voices)} />
              <div className="flex justify-between py-2 font-medium">
                <dt className="text-fg">Distilled total</dt>
                <dd className="text-fg">
                  {fmtBytes(st.total)}
                  {st.original ? <span className="ml-1.5 font-normal text-ok">−{(100 * (1 - st.total / st.original)).toFixed(1)}%</span> : null}
                </dd>
              </div>
            </dl>
            {!meta.extracts_at && st.original_on_disk && !d.shortcut && (
              <div className="border-t border-line py-3">
                <Button size="sm" icon={Camera} onClick={addExtracts}>Create snapshots and voice samples</Button>
              </div>
            )}
          </Panel>
          {zoom && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(16,24,40,0.75)] p-6" onClick={() => setZoom(null)}>
              <div className="max-h-full max-w-6xl" onClick={(e) => e.stopPropagation()}>
                <div className="mb-2 flex items-center justify-between gap-4 text-sm text-white">
                  <span className="tnum">{zoom.caption}</span>
                  <button onClick={() => setZoom(null)} className="rounded-md p-1 hover:bg-white/10" aria-label="Close"><X className="h-5 w-5" /></button>
                </div>
                <img src={zoom.src} alt="" className="max-h-[80vh] w-auto rounded-xl bg-black" />
              </div>
            </div>
          )}
          <Panel title="Processing" bodyClass="px-5 py-2">
            <dl className="tnum divide-y divide-line text-[13px]">
              {Object.entries(m.timings_s)
                .filter(([k]) => k !== "total")
                .map(([k, v]: any) => (
                  <div key={k} className="flex justify-between py-2">
                    <dt className="text-fg-3">{STAGE[k] || k}</dt>
                    <dd className="text-fg">{fmtDuration(v)}</dd>
                  </div>
                ))}
              <div className="flex justify-between py-2 font-medium">
                <dt className="text-fg">Total</dt>
                <dd className="text-fg">{fmtDuration(m.timings_s.total)}</dd>
              </div>
            </dl>
          </Panel>
        </div>
      </div>
    </div>
  );
}

const STAGE: Record<string, string> = {
  probe: "Reading file", frames: "Sampling video", names: "Names on screen", screens: "Shared screens",
  transcribe: "Speech to text", speakers: "Separating speakers", match: "Matching voices to names", save: "Saving",
  keep: "Snapshots and voice samples",
};

function jumpTo(turns: any[], t: number) {
  const line = turns.find((x) => x.end >= t - 1) || turns[turns.length - 1];
  const el = line && document.getElementById(`seg-${line.id}`);
  if (el) {
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("bg-accent-100");
    setTimeout(() => el.classList.remove("bg-accent-100"), 2500);
  }
}

/** Ask a question about this meeting: answered by the local model from the distilled output only, with the lines
 *  it comes from, and a warning for any number or name in the answer that does not occur in the record. */
function AskPanel({ id, turns }: { id: string; turns: any[] }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  async function ask(e?: React.FormEvent) {
    e?.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    setErr(null);
    try {
      setRes({ q, ...(await api.post(`/api/recordings/${id}/ask`, { question: q })) });
    } catch (x: any) {
      setErr(x.message);
    }
    setBusy(false);
  }
  return (
    <Panel title="Ask this meeting" description="Answered by the local AI from the transcript and slides only. Every answer shows where it comes from." className="mb-6">
      <form onSubmit={ask} className="flex flex-wrap gap-2">
        <input className="field min-w-0 flex-1" placeholder="e.g. What was decided about the budget?" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Question about this meeting" />
        <Button variant="primary" icon={busy ? Loader2 : MessageSquare} disabled={busy || !q.trim()} type="submit">{busy ? "Thinking…" : "Ask"}</Button>
      </form>
      {err && <p className="mt-3 text-sm text-bad">{err}</p>}
      {res && (
        <div className="mt-4 rounded-2xl bg-subtle p-4">
          <div className="text-[13px] font-semibold text-fg-3">{res.q}</div>
          <p className="mt-1 text-[15px] leading-6 text-fg">{res.answer}</p>
          {res.found && (
            <div className="mt-2">
              {res.verified ? (
                <Badge tone="green" dot>Every number and name is in the record</Badge>
              ) : (
                <Badge tone="amber" dot>Check: {res.unsupported.join(", ")} not found in the record</Badge>
              )}
            </div>
          )}
          {res.sources?.length > 0 && (
            <div className="mt-3 space-y-1.5 border-t border-line pt-3">
              <div className="text-xs font-bold text-fg-3">Where this comes from</div>
              {res.sources.map((s: any, i: number) => (
                <button key={i} onClick={() => jumpTo(turns, s.time)} className="flex w-full gap-2 rounded-xl px-2 py-1.5 text-left text-[13px] hover:bg-hover" title="Show in the transcript">
                  <span className="tnum shrink-0 font-semibold text-accent-700">{fmtClock(s.time)}</span>
                  <span className="shrink-0 font-semibold text-fg-2">{s.kind === "slide" ? "Slide" : s.speaker}</span>
                  <span className="line-clamp-2 text-fg-3">{s.text}</span>
                </button>
              ))}
            </div>
          )}
          <div className="mt-2 text-[11px] text-fg-4">{res.model}</div>
        </div>
      )}
    </Panel>
  );
}

/** Meeting summary written by the local model (Ministral 3 3B via Ollama), every point checked against the transcript. */
function SummaryPanel({ id, turns }: { id: string; turns: any[] }) {
  const { toast } = useUI();
  const r = usePoll(() => api.get(`/api/recordings/${id}/summary`), 4000, [id]);
  const [open, setOpen] = useState(true);
  const d = r.data;
  if (!d) return null;
  const s = d.summary && !d.summary.error ? d.summary : null;
  const running = d.job && ["queued", "running"].includes(d.job.status);
  const jump = (t: number) => jumpTo(turns, t);
  const At = ({ it }: { it: any }) => (
    <button onClick={() => jump(it.t)} className="tnum mr-2 shrink-0 text-xs font-medium text-accent-600 hover:text-accent-700" title="Show in the transcript">
      {it.time}
    </button>
  );
  async function make() {
    try {
      await api.post(`/api/recordings/${id}/summary`);
      r.reload();
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }
  return (
    <Panel
      title={s?.title ? `Summary · ${s.title}` : "Summary"}
      className="mb-6"
      bodyClass={s && open ? "px-5 py-4" : "px-5 py-3"}
      actions={
        <div className="flex items-center gap-2">
          {s && <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</Button>}
          {d.available && !running && <Button size="sm" variant="ghost" icon={RotateCw} onClick={make}>{s ? "Write again" : "Write summary"}</Button>}
        </div>
      }
    >
      {running ? (
        <p className="flex items-center gap-2 text-sm text-fg-3"><Loader2 className="h-4 w-4 animate-spin text-accent-600" /> Writing the summary with the local model… (about a minute)</p>
      ) : !s ? (
        <p className="text-sm text-fg-3">{d.available ? "No summary yet." : `No AI summary: ${d.message}.`}{d.job?.status === "error" ? ` Last attempt failed: ${d.job.error}` : ""}</p>
      ) : !open ? null : (
        <div className="space-y-4 text-sm">
          {s.purpose && <p className="text-fg-2"><span className="font-medium text-fg">Purpose: </span>{s.purpose}</p>}
          <p className="leading-6 text-fg-2">{s.summary}</p>
          <div className="grid gap-5 lg:grid-cols-2">
            <div>
              <h3 className="mb-1.5 text-[13px] font-semibold text-fg">Decisions</h3>
              {s.decisions?.length ? s.decisions.map((it: any, i: number) => (
                <div key={i} className="flex py-0.5 text-[13px] text-fg-2"><At it={it} />{it.decision}</div>
              )) : <p className="text-[13px] text-fg-3">No decision was taken.</p>}
            </div>
            <div>
              <h3 className="mb-1.5 text-[13px] font-semibold text-fg">Action items</h3>
              {s.action_items?.length ? s.action_items.map((it: any, i: number) => (
                <div key={i} className="flex py-0.5 text-[13px] text-fg-2">
                  <At it={it} />
                  <span><span className="font-medium text-fg">{it.owner || "unclear"}:</span> {it.task}{it.due ? <span className="text-fg-3"> (due {it.due})</span> : null}</span>
                </div>
              )) : <p className="text-[13px] text-fg-3">No action items were agreed.</p>}
            </div>
          </div>
          {s.discussion?.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-[13px] font-semibold text-fg">Discussion</h3>
              {s.discussion.map((it: any, i: number) => (
                <div key={i} className="flex py-0.5 text-[13px] text-fg-2"><At it={it} />{it.point}</div>
              ))}
            </div>
          )}
          {s.open_questions?.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-[13px] font-semibold text-fg">Open questions</h3>
              {s.open_questions.map((it: any, i: number) => (
                <div key={i} className="flex py-0.5 text-[13px] text-fg-2"><At it={it} />{it.question}</div>
              ))}
            </div>
          )}
          <p className="text-xs text-fg-4">
            Written by {s.model} on this computer. Every point is checked against the transcript at the time it names
            {s.removed?.length ? `; ${s.removed.length} unsupported point${s.removed.length === 1 ? " was" : "s were"} removed.` : "."}
          </p>
        </div>
      )}
    </Panel>
  );
}

function Row({ k, v }: { k: string; v: any }) {
  return (
    <div className="flex justify-between gap-3 py-2">
      <dt className="text-fg-3">{k}</dt>
      <dd className="text-right text-fg">{v}</dd>
    </div>
  );
}

function Stat({ label, value, hint }: any) {
  return (
    <div className="panel px-5 py-4">
      <div className="text-[13px] font-medium text-fg-3">{label}</div>
      <div className="tnum mt-1 text-xl font-semibold text-fg">{value}</div>
      {hint && <div className="mt-0.5 truncate text-xs text-fg-3" title={typeof hint === "string" ? hint : undefined}>{hint}</div>}
    </div>
  );
}

function Block({ b, focus, onSave, snaps, fileUrl, onZoom }: { b: any; focus: string | null; onSave: (id: number, text: string) => void; snaps: any[]; fileUrl: (r: string) => string; onZoom: (s: any) => void }) {
  const confs = b.lines.map((l: any) => l.confidence).filter((c: any) => c != null);
  const avg = confs.length ? confs.reduce((a: number, c: number) => a + c, 0) / confs.length : null;
  return (
    <div className="flex gap-4 px-5 py-3.5">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white" style={{ background: colorOf(b.speaker) }}>
        {initials(b.speaker_name)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex flex-wrap items-baseline gap-x-3 text-[13px]">
          <span className="font-semibold text-fg">{b.speaker_name}</span>
          {/^(Speaker \d+|Unknown speaker)$/.test(b.speaker_name) && b.lines.find((t: any) => t.camera_label)?.camera_label && (
            <span className="text-xs font-medium text-fg-3" title="The name label of the camera tile that was lit while this was said. It names the camera or account, not a person.">
              via {b.lines.find((t: any) => t.camera_label).camera_label}'s camera
            </span>
          )}
          <span className="tnum text-fg-3">{fmtClock(b.start)}</span>
          {b.lines.some((t: any) => t.overlap_with?.length) && (
            <span
              className="rounded-md bg-warn-bg px-1.5 py-0.5 text-xs font-medium text-warn"
              title="Seen on screen: this person and the others named here were speaking at the same time. The speech model writes mostly the louder voice, so this text may be incomplete."
            >
              Overlap with {Array.from(new Set(b.lines.flatMap((t: any) => t.overlap_with || []))).join(", ")}
            </span>
          )}
          {b.lines.some((t: any) => t.attribution && t.attribution.status !== "supported" && t.attribution.words > 0 && t.attribution.uncertain_word_count / t.attribution.words >= 0.3) && (
            <span
              className="rounded-md bg-subtle px-1.5 py-0.5 text-xs font-medium text-fg-3"
              title="Some words of this turn have no clear voice evidence (a speaker change, a pause or a very short remark). It is shown under the surrounding voice."
            >
              voice uncertain
            </span>
          )}
          {avg != null && (
            <span className={`tnum text-xs ${avg < 0.7 ? "font-medium text-warn" : "text-fg-4"}`} title="Average speech recognition confidence of this turn">
              {Math.round(avg * 100)}%
            </span>
          )}
        </div>
        <p className="text-sm leading-6 text-fg-2">
          {b.lines.map((t: any) => <Line key={t.id} t={t} focused={String(t.id) === focus} onSave={onSave} />)}
        </p>
        {snaps.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-3">
            {snaps.map((s: any) => (
              <button key={s.idx} onClick={() => onZoom(s)} className="group overflow-hidden rounded-xl border border-line bg-subtle text-left hover:border-line-strong" title="Open snapshot">
                <img src={fileUrl(s.image)} alt={`Screen at ${fmtClock(s.t)}`} loading="lazy" className="block h-36 w-auto max-w-[320px] object-contain" />
                <div className="flex items-center gap-1.5 border-t border-line px-2.5 py-1.5 text-xs text-fg-3">
                  <Camera className="h-3.5 w-3.5" />
                  <span className="tnum">{fmtClock(s.t)}</span>
                  <span className="truncate">“{s.phrase}”</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Line({ t, focused, onSave }: { t: any; focused: boolean; onSave: (id: number, text: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState(t.text);
  const low = t.confidence != null && t.confidence < 0.7;
  if (edit)
    return (
      <span className="my-1 block space-y-2">
        <textarea autoFocus className="field-area w-full" rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
        <span className="flex gap-2">
          <Button size="sm" variant="primary" onClick={() => { onSave(t.id, draft); setEdit(false); }}>Save</Button>
          <Button size="sm" onClick={() => { setDraft(t.text); setEdit(false); }}>Cancel</Button>
        </span>
      </span>
    );
  return (
    <>
      <span
        id={`seg-${t.id}`}
        onDoubleClick={() => setEdit(true)}
        title={`${fmtClock(t.start)}${t.confidence != null ? ` · ${Math.round(t.confidence * 100)}% confidence` : ""} · double-click to correct`}
        className={`cursor-text rounded-sm ${focused ? "bg-accent-100" : "hover:bg-subtle"} ${low ? "underline decoration-warn-bar decoration-dotted underline-offset-4" : ""}`}
      >
        {t.text}
      </span>
      {t.edited ? <span className="ml-1 text-xs text-fg-4" title={`Original: ${t.original_text}`}>(edited)</span> : null}{" "}
    </>
  );
}

function initials(name: string) {
  const m = name.match(/^Speaker (\d+)$/);
  if (m) return m[1];
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

function ScreenCard({ s, fileUrl, onZoom }: { s: any; fileUrl: (r: string) => string; onZoom: (z: any) => void }) {
  const [open, setOpen] = useState(false);
  const lines = (s.text || "").split("\n").map((l: string) => l.trim()).filter(Boolean);
  // key frame of an unknown screen, or the page of the document a matched slide comes from
  const thumb = s.image ? fileUrl(s.image) : s.page_id && s.page_thumb ? `/api/pages/${s.page_id}/image?thumb=1` : null;
  const full = s.image ? fileUrl(s.image) : s.page_id && s.page_image ? `/api/pages/${s.page_id}/image` : null;
  const caption = `${s.title || "Shared screen"} · ${s.source ? `${s.source.doc}, slide ${s.source.page} (document page)` : "key frame from the video"}${s.slide_uid ? ` · slide id ${s.slide_uid}` : ""}`;
  return (
    <div className="bg-subtle px-5 py-3">
      <div className="flex items-start gap-3">
        {thumb ? (
          <button onClick={() => onZoom({ src: full, caption })} className="shrink-0 overflow-hidden rounded-md border border-line bg-surface hover:border-line-strong" title="Open image">
            <img src={thumb} alt="" loading="lazy" className="block h-[68px] w-[120px] object-contain" />
          </button>
        ) : (
          <MonitorUp className="mt-0.5 h-4 w-4 shrink-0 text-fg-3" />
        )}
        <div className="min-w-0 flex-1 text-[13px]">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <span className="font-medium text-fg">{s.title || "Screen share"}</span>
            <span className="tnum text-fg-3">{fmtClock(s.t_start)}–{fmtClock(s.t_end)}</span>
            {s.slide_uid && <span className="font-mono text-[11px] text-fg-4" title="Slide id: the same slide anywhere gets the same id">{s.slide_uid}</span>}
            {s.source ? (
              <span className="text-fg-3">
                {s.source.doc}, slide {s.source.page}
                {s.source.doc_no ? ` · ${s.source.doc_no} rev ${s.source.rev}` : ""}
              </span>
            ) : (
              <Badge tone="amber">Not in any uploaded document</Badge>
            )}
          </div>
          {!lines.length && !s.source && <div className="mt-1 text-xs text-fg-3">Text on this screen is not readable at the video's resolution.</div>}
          {lines.length > 1 && (
            <>
              <button className="mt-1 text-xs font-medium text-accent-600 hover:text-accent-700" onClick={() => setOpen(!open)}>
                {open ? "Hide slide text" : `Show slide text (${lines.length} lines)`}
              </button>
              {open && <div className="mt-2 whitespace-pre-line rounded-md border border-line bg-surface px-3 py-2 text-[13px] leading-5 text-fg-2">{lines.join("\n")}</div>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Speakers({ id, d, reload, fileUrl }: { id: string; d: any; reload: () => void; fileUrl: (r: string) => string }) {
  const { toast } = useUI();
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => audio.current?.pause(), []);
  function play(s: any) {
    audio.current?.pause();
    if (playing === s.id) return setPlaying(null);
    const a = new Audio(fileUrl(s.voice.file));
    a.onended = () => setPlaying(null);
    a.play().catch((e) => toast(e.message, "bad"));
    audio.current = a;
    setPlaying(s.id);
  }
  const [names, setNames] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  useEffect(() => setNames(Object.fromEntries(d.speakers.map((s: any) => [s.id, s.name || ""]))), [d]);
  const total = d.speakers.reduce((a: number, s: any) => a + (s.talk_time_s || 0), 0) || 1;
  const sourceLabel = (s: any) =>
    !s.source
      ? "Voice only"
      : s.source === "captions"
        ? "Teams captions"
        : s.source === "manual"
          ? "Named manually"
          : s.source === "said their name"
            ? "Said their name"
            : s.source === "called by name"
              ? "Called by name by others"
              : s.source === "soundtrack"
                ? "Music or video played in the meeting"
            : s.source.includes("initials")
              ? "Camera-off initials on screen"
              : "Name label on screen";

  async function save() {
    try {
      await api.patch(`/api/recordings/${id}/speakers`, names);
      toast("Speaker names saved. Exports updated.");
      setEditing(false);
      reload();
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }

  return (
    <Panel
      title="Speakers"
      bodyClass="px-5 py-3"
      actions={editing ? <Button size="sm" variant="primary" onClick={save}>Save</Button> : d.speakers.length > 0 && <Button size="sm" variant="ghost" icon={Pencil} onClick={() => setEditing(true)}>Edit names</Button>}
    >
      {!d.speakers.length && <p className="text-sm text-fg-3">No speakers detected.</p>}
      <div className="space-y-3">
        {d.speakers.map((s: any) => (
          <div key={s.id} className="flex items-center gap-3">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: colorOf(s.id) }} />
            <div className="min-w-0 flex-1">
              {editing ? (
                <input className="field h-8 w-full" list="participants" value={names[s.id] ?? ""} placeholder={`Speaker ${s.id.slice(1)}`} onChange={(e) => setNames({ ...names, [s.id]: e.target.value })} />
              ) : (
                <>
                  <div className="truncate text-sm font-medium text-fg">{s.label}</div>
                  <div className="text-xs text-fg-3" title={s.score != null ? `Match score ${s.score}` : undefined}>{sourceLabel(s)}{s.camera?.label ? ` · speaks through ${s.camera.label}'s camera` : s.camera ? (s.camera.room ? " · person in the room camera" : " · camera picture") : ""}</div>
                  {!s.name && s.account_label && (
                    <div className="text-xs text-warn" title="This camera or account label is lit or active for several voices, so it names the camera, not a person.">
                      Shared camera / account: {s.account_label}
                      {d.speakers.filter((x: any) => x.camera?.label === s.account_label).length > 1 &&
                        ` · ${d.speakers.filter((x: any) => x.camera?.label === s.account_label).length} people speak through it`}
                    </div>
                  )}
                  {!s.name && s.candidate_name && !s.account_label && (
                    <div className="text-xs text-warn" title="Weak evidence only (for example movement in that person's tile). Not used as the name. Type the name above if it is right.">
                      Possibly {s.candidate_name}
                    </div>
                  )}
                </>
              )}
            </div>
            {s.voice && !editing && (
              <button
                onClick={() => play(s)}
                title={`Voice sample: ${s.voice.seconds} s of the clearest speech (${fmtBytes(s.voice.bytes)})`}
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ${playing === s.id ? "border-accent-500 bg-accent-50 text-accent-700" : "border-line text-fg-3 hover:bg-subtle hover:text-fg-2"}`}
              >
                {playing === s.id ? <Pause className="h-3.5 w-3.5" /> : <Play className="ml-0.5 h-3.5 w-3.5" />}
              </button>
            )}
            <span className="tnum w-9 text-right text-xs text-fg-3">{Math.round((100 * (s.talk_time_s || 0)) / total)}%</span>
          </div>
        ))}
      </div>
      {d.participants.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <div className="mb-1.5 text-xs font-medium text-fg-3">Names seen on screen</div>
          <div className="flex flex-wrap gap-1.5">
            {d.participants.map((p: string) => <Badge key={p}>{p}</Badge>)}
          </div>
        </div>
      )}
      <datalist id="participants">{d.participants.map((p: string) => <option key={p} value={p} />)}</datalist>
    </Panel>
  );
}

/* ================================================================== document */
function DocumentView({ id }: { id: string }) {
  const doc = usePoll(() => api.get(`/api/documents/${id}`), 0, [id]);
  const [params] = useSearchParams();
  const page = params.get("page");
  const d = doc.data;
  useEffect(() => {
    if (d && page) setTimeout(() => document.getElementById(`page-${page}`)?.scrollIntoView({ block: "center" }), 50);
  }, [d, page]);
  if (doc.error) return <ErrorNote error={doc.error} />;
  if (!d) return <Loading />;
  return (
    <div className="max-w-4xl">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-1.5 text-[13px] text-fg-3"><Folder className="h-3.5 w-3.5" /> {d.folder}</div>
          <h1 className="truncate text-[30px] font-extrabold leading-9 tracking-[-0.02em]">{d.title || d.name}</h1>
          <div className="mt-1 flex flex-wrap gap-x-4 text-[13px] text-fg-3">
            <span>{d.name}</span>
            {d.doc_no && <span>{d.doc_no}</span>}
            {d.rev && <span>Rev {d.rev}</span>}
            {d.author && <span>{d.author}</span>}
            {d.classification && <span>{d.classification}</span>}
            <span>{d.pages.length} slides</span>
          </div>
        </div>
        <div className="flex gap-2">
          <Button icon={ScrollText} href={`/api/report/${id}`}>Report (PDF)</Button>
          <Button variant="primary" icon={FileJson} href={`/api/export/${id}`}>Download JSON</Button>
        </div>
      </div>
      {d.shortcut && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-accent-100 bg-accent-50 px-4 py-3 text-sm text-fg-2">
          <Link2 className="mt-0.5 h-4 w-4 shrink-0 text-accent-600" />
          <div>
            <span className="font-medium text-fg">Shortcut.</span> Identical to <Link className="link" to={`/transcripts/${d.file.id}`}>{d.name}</Link> in {d.folder}; no second copy is stored.
          </div>
        </div>
      )}
      <div className="panel divide-y divide-line">
        {d.pages.map((p: any) => (
          <div key={p.page} id={`page-${p.page}`} className={`px-5 py-4 ${String(p.page) === page ? "bg-accent-50" : ""}`}>
            <div className="mb-1 flex items-baseline gap-3 text-[13px]">
              <span className="tnum font-semibold text-fg">Slide {p.page}</span>
              {p.title && <span className="text-fg-2">{p.title}</span>}
            </div>
            {p.text ? <PageText p={p} /> : <p className="text-sm text-fg-4">No text on this slide</p>}
          </div>
        ))}
      </div>
    </div>
  );
}

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();

/** A page's text line by line; a line that also appears on other slides, decks or PDFs links to those places. */
function PageText({ p }: { p: any }) {
  const [open, setOpen] = useState<string | null>(null);
  const units: any[] = p.lines || [];
  return (
    <div className="space-y-0.5 text-sm leading-6 text-fg-2">
      {p.text.split("\n").filter((l: string) => l.trim()).map((raw: string, i: number) => {
        const hits = units.filter((u) => norm(raw).includes(norm(u.text)));
        const footer = hits.length > 0 && hits.every((u) => u.footer);
        const refs = footer ? [] : hits.flatMap((u) => u.also_in.map((o: any) => ({ ...o, line: u.text })));
        const key = `${i}`;
        return (
          <div key={i}>
            <div className={`flex flex-wrap items-baseline gap-x-2 ${footer ? "text-xs text-fg-4" : ""}`}>
              <span>{raw}</span>
              {footer && <span className="text-[11px] font-semibold">· footer on most pages</span>}
              {refs.length > 0 && (
                <button
                  onClick={() => setOpen(open === key ? null : key)}
                  aria-expanded={open === key}
                  className="inline-flex h-5 items-center gap-1 rounded-full bg-accent-50 px-2 text-[11px] font-bold text-accent-700 hover:bg-accent-100"
                  title="The same text is stored once and referenced from each place"
                >
                  <Link2 className="h-3 w-3" />
                  also in {refs.length} {refs.length === 1 ? "place" : "places"}
                </button>
              )}
            </div>
            {open === key && (
              <div className="mb-1.5 ml-1 mt-1 flex flex-wrap gap-1.5 border-l-2 border-accent-100 pl-3">
                {refs.map((o: any, k: number) => (
                  <Link key={k} to={`/transcripts/${o.file_id}?page=${o.page}`} title={o.folder}
                    className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-xs font-semibold text-fg-2 hover:border-accent-500 hover:text-accent-700">
                    <FileText className="h-3.5 w-3.5 text-fg-4" />
                    {/\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(o.document) ? o.document : <>{o.document} · {/\.pdf$/i.test(o.document) ? "page" : "slide"} {o.page}</>}
                  </Link>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
