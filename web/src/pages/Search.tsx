import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight, FileText, Film, Folder, MessageSquareQuote, MonitorUp, Search as SearchIcon } from "lucide-react";
import { api, fmtClock } from "../api";
import { Button, EmptyState, ErrorNote, Highlight, usePoll } from "../ui";

export default function Search() {
  const [params, setParams] = useSearchParams();
  const q0 = params.get("q") || "";
  const [q, setQ] = useState(q0);
  const [type, setType] = useState(params.get("type") || "");
  const [folder, setFolder] = useState(params.get("folder") || "");
  const [speaker, setSpeaker] = useState(params.get("speaker") || "");
  const [res, setRes] = useState<any>(null);
  const [people, setPeople] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const folders = usePoll(() => api.get("/api/folders"), 0);

  async function run(query: string, t: string, fo: string, sp: string = speaker) {
    if (!query.trim()) return;
    setBusy(true);
    setErr(null);
    setPeople([]);
    api.get(`/api/people?q=${encodeURIComponent(query)}`).then(setPeople).catch(() => {});   // instant: shown while the ranking is still working
    try {
      const qs = new URLSearchParams({ q: query, ...(t ? { type: t } : {}), ...(fo ? { folder: fo } : {}), ...(sp ? { speaker: sp } : {}) });
      setRes(await api.get(`/api/search?${qs}`));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (q0) run(q0, type, folder);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = (e?: any) => {
    e?.preventDefault();
    setParams({ q, ...(type ? { type } : {}), ...(folder ? { folder } : {}), ...(speaker ? { speaker } : {}) });
    run(q, type, folder, speaker);
  };

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="text-[30px] font-extrabold leading-9 tracking-[-0.02em]">Search</h1>
      <p className="mb-6 mt-1 text-sm text-fg-3">Search what was said and what was shown, across all transcripts and documents. Matches meaning, not only exact words.</p>
      <form onSubmit={submit} className="panel mb-6 flex flex-wrap items-center gap-3 p-2 pl-4">
        <SearchIcon className="h-5 w-5 shrink-0 text-fg-4" />
        <input autoFocus className="h-10 min-w-[240px] flex-1 bg-transparent text-[15px] text-fg outline-none placeholder:text-fg-4" placeholder="Search transcripts and documents" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="field" value={folder} onChange={(e) => { setFolder(e.target.value); if (q.trim()) run(q, type, e.target.value); }}>
          <option value="">All folders</option>
          {(folders.data || []).map((f: any) => <option key={f.name} value={f.name}>{f.name}</option>)}
        </select>
        <select className="field" value={speaker} onChange={(e) => { setSpeaker(e.target.value); if (q.trim()) run(q, type, folder, e.target.value); }} title="Only what this person said">
          <option value="">Anyone</option>
          {(res?.speakers || []).map((n: string) => <option key={n} value={n}>{n}</option>)}
        </select>
        <div className="flex rounded-xl border border-line bg-subtle p-0.5 text-[13px]">
          {[["", "All"], ["recording", "Transcripts"], ["page", "Documents and slides"]].map(([v, l]) => (
            <button type="button" key={v} onClick={() => { setType(v); if (q.trim()) run(q, v, folder); }}
              className={`rounded-md px-3 py-1.5 font-medium ${type === v ? "bg-surface text-fg shadow-sm ring-1 ring-line" : "text-fg-3 hover:text-fg-2"}`}>
              {l}
            </button>
          ))}
        </div>
      </form>
      <ErrorNote error={err} />
      {busy && <div className="py-10 text-center text-sm text-fg-3">Searching… the first search after a restart takes a few seconds while the models load.</div>}
      {people.length > 0 && <PeoplePanel people={people} />}
      {!busy && res && !res.results.length && !people.length && <div className="panel"><EmptyState icon={SearchIcon} title="No results" description={type ? `Nothing in ${type === "page" ? "the documents and slides" : "the transcripts"} matches. It may be ${type === "page" ? "said in a recording" : "in a document or on a slide"}: search everything.` : "Try other words."} action={type ? <Button onClick={() => { setType(""); run(q, "", folder, speaker); }}>Search everything</Button> : undefined} /></div>}
      {!busy && res?.results.length > 0 && (
        <>
          <div className="tnum mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-fg-3">
            <span>{res.results.length} results</span>
            {res.interpreted?.speaker && <span className="rounded-md bg-accent-100 px-2 py-0.5 font-medium text-accent-700">Only what {res.interpreted.speaker} said{res.interpreted.query !== q ? ` about “${res.interpreted.query}”` : ""}</span>}
            {Object.entries(res.interpreted?.also_matched || {}).map(([w, near]: any) => <span key={w}>“{w}” also matched {near.join(", ")}</span>)}
          </div>
          <div className="panel divide-y divide-line">
            {res.results.map((h: any, i: number) => {
              const rec = h.type === "recording";
              const scr = h.type === "screen";                       // a slide shown in a recording (not a page of an uploaded document)
              const href = rec || scr ? `/transcripts/${h.file_id}${h.seg_id ? `?seg=${h.seg_id}` : ""}` : `/transcripts/${h.file_id}?page=${h.page}`;
              const Icon = rec ? Film : scr ? MonitorUp : FileText;
              return (
                <Link key={i} to={href} className="flex gap-4 px-5 py-4 transition hover:bg-subtle">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-line bg-subtle">
                    <Icon className="h-4 w-4 text-fg-3" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                      <span className="font-medium text-fg">{h.name}</span>
                      {rec || scr ? <span className="tnum font-medium text-accent-600">{fmtClock(h.t)}</span> : <span className="text-fg-3">Slide {h.page}</span>}
                      {scr && <span className="rounded-md bg-accent-100 px-2 py-0.5 text-xs font-medium text-accent-700">Slide shown in the recording</span>}
                      <span className="inline-flex items-center gap-1 text-fg-3"><Folder className="h-3.5 w-3.5" />{h.folder}</span>
                      {h.copies && <span className="text-fg-3">+{h.copies.length} identical</span>}
                    </div>
                    <p className="mt-1.5 text-sm leading-6 text-fg-2">
                      {h.on_slide && <span className="font-medium text-fg">On the slide: </span>}
                      {h.speaker && <span className="font-medium text-fg">{h.speaker}: </span>}
                      <Highlight text={h.snippet || ""} query={q} />
                    </p>
                    {h.source && <div className="mt-1 text-xs text-fg-3">On screen: {h.source.doc}, slide {h.source.page}</div>}
                  </div>
                  {h.image && (
                    <img src={h.image} alt={rec || scr ? "On screen at this moment" : `Slide ${h.page}`} loading="lazy"
                      className="hidden h-[72px] w-[128px] shrink-0 rounded-md border border-line bg-subtle object-contain sm:block" />
                  )}
                </Link>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ a person: everything they said, per recording */
const initials = (n: string) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
const mins = (sec: number) => (sec >= 90 ? `${Math.round(sec / 60)} min` : `${Math.round(sec)} s`);

function PeoplePanel({ people }: { people: any[] }) {
  return (
    <div className="mb-6 space-y-4">
      {people.map((p) => <PersonCard key={p.name} p={p} />)}
    </div>
  );
}

function PersonCard({ p }: { p: any }) {
  const [open, setOpen] = useState<number | null>(p.files.length === 1 ? p.files[0].file_id : null);
  return (
    <section className="panel overflow-hidden">
      <header className="flex items-center gap-4 border-b border-line px-5 py-4">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent-100 text-base font-extrabold text-accent-700">{initials(p.name)}</div>
        <div className="min-w-0">
          <div className="text-lg font-extrabold leading-6">{p.name}</div>
          <div className="text-[13px] text-fg-3">
            {p.lines} line{p.lines === 1 ? "" : "s"} · {mins(p.talk_s)} of speech · {p.files.length} recording{p.files.length === 1 ? "" : "s"}. Pick a recording to see everything this person said in it.
          </div>
        </div>
      </header>
      <div className="divide-y divide-line">
        {p.files.map((f: any) => (
          <div key={f.file_id}>
            <button type="button" onClick={() => setOpen(open === f.file_id ? null : f.file_id)} className="flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-subtle">
              {open === f.file_id ? <ChevronDown className="h-4 w-4 text-fg-4" /> : <ChevronRight className="h-4 w-4 text-fg-4" />}
              <Film className="h-4 w-4 shrink-0 text-fg-3" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{f.name}</span>
              <span className="tnum text-xs text-fg-3">{f.lines} lines · {mins(f.talk_s)}</span>
              <span className="inline-flex items-center gap-1 text-xs text-fg-3"><Folder className="h-3.5 w-3.5" />{f.folder}</span>
            </button>
            {open === f.file_id && <PersonLines name={p.name} fileId={f.file_id} />}
          </div>
        ))}
      </div>
    </section>
  );
}

function PersonLines({ name, fileId }: { name: string; fileId: number }) {
  const d = usePoll(() => api.get(`/api/person?name=${encodeURIComponent(name)}&file_id=${fileId}`), 0, [name, fileId]);
  const [showMentions, setShowMentions] = useState(false);
  if (d.error) return <div className="px-5 pb-4"><ErrorNote error={d.error} /></div>;
  if (!d.data) return <div className="px-5 pb-4 text-sm text-fg-3">Loading…</div>;
  const x = d.data;
  return (
    <div className="bg-subtle/50 px-5 pb-5 pt-3">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-[13px] text-fg-3">
        <span>{x.lines.length} lines · {mins(x.talk_s)} of speech{x.slides ? ` · over ${x.slides} slide${x.slides === 1 ? "" : "s"}` : ""}</span>
        <span className="ml-auto flex gap-2">
          <Button size="sm" href={`/transcripts/${fileId}`}>Open full transcript</Button>
          <Button size="sm" icon={FileText} href={`/api/person/export?name=${encodeURIComponent(name)}&file_id=${fileId}`}>Download these lines</Button>
        </span>
      </div>
      <div className="space-y-1">
        {x.lines.map((ln: any) => (
          <div key={ln.id}>
            {ln.slide?.change && (
              <div className="mb-1 mt-3 flex items-center gap-3 rounded-lg border border-line bg-surface p-2">
                {ln.slide.image && <img src={ln.slide.image} alt="" loading="lazy" className="h-12 w-20 shrink-0 rounded border border-line object-contain" />}
                <div className="flex items-center gap-2 text-xs text-fg-3"><MonitorUp className="h-3.5 w-3.5" /> On screen{ln.slide.title ? `: ${ln.slide.title}` : ""}</div>
              </div>
            )}
            <Link to={`/transcripts/${fileId}?seg=${ln.id}`} className="flex gap-3 rounded-lg px-2 py-1.5 hover:bg-surface">
              <span className="tnum w-16 shrink-0 text-xs font-medium text-accent-600">{fmtClock(ln.start)}</span>
              <span className="text-sm leading-6 text-fg-2">{ln.text}</span>
            </Link>
          </div>
        ))}
      </div>
      {x.mentions.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <button type="button" onClick={() => setShowMentions(!showMentions)} className="flex items-center gap-2 text-[13px] font-medium text-fg-2 hover:text-fg">
            <MessageSquareQuote className="h-4 w-4" /> Said about {name.split(" ")[0]} by others ({x.mentions.length})
            {showMentions ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
          {showMentions && (
            <div className="mt-2 space-y-1">
              {x.mentions.map((m: any) => (
                <Link key={m.id} to={`/transcripts/${fileId}?seg=${m.id}`} className="flex gap-3 rounded-lg px-2 py-1.5 hover:bg-surface">
                  <span className="tnum w-16 shrink-0 text-xs font-medium text-accent-600">{fmtClock(m.start)}</span>
                  <span className="text-sm leading-6 text-fg-2"><b className="font-semibold text-fg">{m.speaker}:</b> {m.text}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
