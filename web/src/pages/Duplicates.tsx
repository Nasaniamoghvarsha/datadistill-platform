import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Copy, Download, FileText, Film, Layers, Link2, Quote } from "lucide-react";
import { api, fmtBytes } from "../api";
import { Badge, Button, EmptyState, Loading, Metric, PageHeader, Panel, usePoll, useUI } from "../ui";

export default function Duplicates() {
  const { confirm, toast } = useUI();
  const r = usePoll(() => api.get("/api/duplicates"), 0);
  const d = r.data;
  if (!d) return <div className="px-8 py-7"><Loading /></div>;
  const t = d.totals;

  async function resolve(p: any, m: any) {
    const ok = await confirm({
      title: `Replace ${m.name} with a shortcut?`,
      body: `Its bytes (${fmtBytes(m.size)}) are deleted. The shortcut stays in ${m.system} and opens the transcript of the copy that is kept. This cannot be undone.`,
      confirm: "Replace with shortcut",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.post("/api/duplicates/resolve", { file_id: m.id, master_id: p.canonical });
      toast(`${m.name} replaced by a shortcut (${fmtBytes(m.size)} freed)`);
      r.reload();
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }

  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 1)], { type: "application/json" }));
    a.download = "duplicates-report.json";
    a.click();
  };

  return (
    <div>
      <PageHeader
        title="Duplicates"
        description="The same content in several places: identical files, the same recording in another file, presentation revisions, decks that share slides, and repeated lines of text."
        actions={<Button icon={Download} onClick={download}>Download report (JSON)</Button>}
      />
      <div className="mb-6 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Metric label="Stored once" value={t.shortcuts} hint={`copies replaced by shortcuts · ${fmtBytes(t.saved_bytes)} saved`} tone="ok" />
        <Metric label="Waiting for a decision" value={t.pending} hint={t.pending ? `${fmtBytes(t.pending_bytes)} can be freed` : "nothing to decide"} tone={t.pending ? "warn" : undefined} />
        <Metric label="Presentation revisions" value={t.families} hint={t.families ? "families of versions" : "none found"} />
        <Metric label="Decks sharing slides" value={t.shared_pairs} hint={d.slide_store.slide_occurrences ? `${d.slide_store.unique_slides} unique of ${d.slide_store.slide_occurrences} slides` : undefined} />
      </div>

      {d.pending.length > 0 && (
        <Panel title="Waiting for a decision" description="Keep one copy, replace the others with shortcuts" className="mb-6" bodyClass="divide-y divide-line">
          {d.pending.map((p: any, i: number) => (
            <div key={i} className="px-5 py-4">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px]">
                <Badge tone="amber" dot>{p.type === "exact" ? "Identical files" : "Same recording, other file"}</Badge>
                <span className="text-fg-3">{p.evidence}</span>
                <span className="ml-auto tnum text-fg-3">{fmtBytes(p.reclaimable_bytes)} can be freed</span>
              </div>
              {p.members.map((m: any) => (
                <div key={m.id} className="flex items-center gap-3 py-1.5 text-sm">
                  <KindIcon kind={m.kind} />
                  <Link to={`/transcripts/${m.id}`} className="min-w-0 truncate font-medium text-fg hover:text-accent-700">{m.name}</Link>
                  <span className="truncate text-[13px] text-fg-3">{m.system}</span>
                  <span className="tnum text-xs text-fg-3">{fmtBytes(m.size)}</span>
                  <span className="ml-auto">
                    {m.id === p.canonical ? <Badge tone="green">Kept</Badge> : <Button size="sm" icon={Link2} onClick={() => resolve(p, m)}>Replace with shortcut</Button>}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </Panel>
      )}

      <Panel title="Stored once" description="Where every copy lives, and the one copy that holds the content" className="mb-6" bodyClass={d.stored_once.length ? "divide-y divide-line" : ""}>
        {!d.stored_once.length ? (
          <EmptyState icon={CheckCircle2} title="No copies yet" description="When the same file or recording is added twice, the second one becomes a shortcut and shows up here." />
        ) : (
          d.stored_once.map((g: any) => (
            <div key={g.master.id} className="px-5 py-4">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <KindIcon kind={g.master.kind} />
                <Link to={`/transcripts/${g.master.id}`} className="font-medium text-fg hover:text-accent-700">{g.master.name}</Link>
                <span className="text-[13px] text-fg-3">{g.master.folder}</span>
                <Badge tone="green">{g.master.original_deleted ? "Transcript kept, video deleted" : "Holds the content"}</Badge>
                <span className="ml-auto tnum text-[13px] font-medium text-ok">{fmtBytes(g.saved_bytes)} saved</span>
              </div>
              <div className="ml-7 mt-2 space-y-1 border-l border-line pl-4">
                {g.copies.map((c: any) => (
                  <div key={c.id} className="flex flex-wrap items-center gap-2 text-[13px]">
                    <Link2 className="h-3.5 w-3.5 text-fg-4" />
                    <span className="text-fg-2">{c.name}</span>
                    <span className="text-fg-3">in {c.folder}</span>
                    <span className="text-fg-4">· {c.how}</span>
                    <span className="ml-auto tnum text-fg-3">{fmtBytes(c.size)}</span>
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </Panel>

      <Panel title="Compare two documents" description="Page by page: which pages are the same, edited, moved, or only in one of them (PDF, PowerPoint or picture)" className="mb-6">
        <Compare docs={d.documents || []} />
      </Panel>

      <Panel title="Presentation revisions" description="Versions of the same deck (same document number, or at least half the slides in common). Unchanged slides are stored once." className="mb-6" bodyClass={d.families.length ? "divide-y divide-line" : ""}>
        {!d.families.length ? (
          <EmptyState icon={Layers} title="No revisions found" description="Upload two versions of a presentation to see which slides changed." />
        ) : (
          d.families.map((fam: any) => <Family key={fam.key} fam={fam} />)
        )}
      </Panel>

      <Panel title="Decks that share slides" description="Different presentations that reuse the same slides" bodyClass={d.shared.length ? "" : ""}>
        {!d.shared.length ? (
          <EmptyState icon={Copy} title="No shared slides" description="Slides are compared by their text and picture fingerprints." />
        ) : (
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Deck</th>
                <th className="th">Shares with</th>
                <th className="th text-right">Shared slides</th>
              </tr>
            </thead>
            <tbody>
              {d.shared.map((p: any, i: number) => (
                <tr key={i}>
                  <td className="td">
                    <Link to={`/transcripts/${p.a.id}`} className="font-medium text-fg hover:text-accent-700">{p.a.name}</Link>
                    <div className="text-xs text-fg-3">{p.a.system} · slides {p.a_pages.join(", ")}</div>
                  </td>
                  <td className="td">
                    <Link to={`/transcripts/${p.b.id}`} className="font-medium text-fg hover:text-accent-700">{p.b.name}</Link>
                    <div className="text-xs text-fg-3">{p.b.system} · slides {p.b_pages.join(", ")}</div>
                  </td>
                  <td className="td tnum text-right font-medium">{p.shared}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {d.repeated_text && <RepeatedText rt={d.repeated_text} />}
    </div>
  );
}

const unitOf = (name: string) => (/\.pdf$/i.test(name) ? "page" : /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(name) ? "image" : "slide");
const placeLabel = (o: any) => (unitOf(o.name) === "image" ? o.name : `${o.name} · ${unitOf(o.name)} ${o.page}`);

function Places({ list }: { list: any[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {list.map((o: any, i: number) => (
        <Link key={i} to={`/transcripts/${o.file_id}?page=${o.page}`} title={o.folder}
          className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-xs font-semibold text-fg-2 hover:border-accent-500 hover:text-accent-700">
          <FileText className="h-3.5 w-3.5 text-fg-4" />
          {placeLabel(o)}
        </Link>
      ))}
    </div>
  );
}

function RepeatedText({ rt }: { rt: any }) {
  const [all, setAll] = useState(false);
  const [footers, setFooters] = useState(false);
  const t = rt.totals;
  const shown = all ? rt.groups : rt.groups.slice(0, 12);
  return (
    <Panel
      title="Repeated text"
      description={`The same title, bullet or sentence on several slides, decks or PDFs. Each line is stored once; every place refers to the others. ${t.unique} unique lines of ${t.units} · ${t.saving_pct}% less text when stored once.`}
      className="mt-6"
      bodyClass={rt.groups.length ? "divide-y divide-line" : ""}
    >
      {!rt.groups.length ? (
        <EmptyState icon={Quote} title="No repeated text" description="Lines are compared after ignoring case, bullets and punctuation. Numbers must match." />
      ) : (
        <>
          {shown.map((g: any) => (
            <div key={g.id} className="flex flex-col gap-2 px-[22px] py-3.5 lg:flex-row lg:items-start lg:gap-5">
              <div className="min-w-0 lg:w-[38%]">
                <div className="text-sm font-semibold text-fg">“{g.text}”</div>
                <div className="mt-0.5 text-xs text-fg-3">
                  {g.count} places{g.documents > 1 ? ` in ${g.documents} documents` : " in one document"}
                </div>
              </div>
              <div className="min-w-0 flex-1"><Places list={g.occurrences} /></div>
            </div>
          ))}
          {rt.groups.length > 12 && (
            <div className="px-[22px] py-3">
              <button className="link text-[13px]" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${rt.groups.length}`}</button>
            </div>
          )}
        </>
      )}
      {rt.footers.length > 0 && (
        <div className="border-t border-line px-[22px] py-3">
          <button className="text-[13px] font-semibold text-fg-3 hover:text-fg" onClick={() => setFooters(!footers)}>
            {footers ? "Hide" : "Show"} {rt.footers.length} footer line{rt.footers.length === 1 ? "" : "s"} (on most pages of a document)
          </button>
          {footers && (
            <div className="mt-2 space-y-2">
              {rt.footers.map((g: any) => (
                <div key={g.id} className="text-[13px] text-fg-3">
                  <span className="font-semibold text-fg-2">“{g.text}”</span> · {g.count} pages in {[...new Set(g.occurrences.map((o: any) => o.name))].join(", ")}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function KindIcon({ kind }: { kind?: string }) {
  const I = kind === "video" ? Film : FileText;
  return <I className="h-4 w-4 shrink-0 text-fg-3" />;
}

const STATE: Record<string, { label: string; cls: string }> = {
  same: { label: "same", cls: "bg-subtle text-fg-3 ring-1 ring-inset ring-line" },
  edited: { label: "edited", cls: "bg-accent-600 font-semibold text-on-accent" },
  moved: { label: "moved", cls: "bg-accent-100 text-accent-700 ring-1 ring-inset ring-accent-500" },
  only_a: { label: "only in the first", cls: "bg-bad/10 text-bad ring-1 ring-inset ring-bad/30" },
  only_b: { label: "only in the second", cls: "bg-ok/10 text-ok ring-1 ring-inset ring-ok/30" },
};

function Compare({ docs }: { docs: any[] }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api.get(`/api/duplicates/compare?a=${a}&b=${b}`));
    } catch (e: any) {
      setRes(null);
      setErr(e.message);
    }
    setBusy(false);
  }
  const label = (x: any) => `${x.name} · ${x.system} · ${x.pages} ${x.pages === 1 ? "page" : "pages"}`;
  if (docs.length < 2) return <EmptyState icon={Layers} title="Two documents needed" description="Upload and process at least two PDFs, presentations or pictures to compare them." />;
  return (
    <div className="px-5 py-4">
      <div className="flex flex-wrap items-center gap-3">
        <select className="field w-72" value={a} onChange={(e) => setA(e.target.value)} aria-label="First document">
          <option value="">First document</option>
          {docs.map((x) => <option key={x.id} value={x.id}>{label(x)}</option>)}
        </select>
        <select className="field w-72" value={b} onChange={(e) => setB(e.target.value)} aria-label="Second document">
          <option value="">Second document</option>
          {docs.filter((x) => String(x.id) !== a).map((x) => <option key={x.id} value={x.id}>{label(x)}</option>)}
        </select>
        <Button variant="primary" disabled={!a || !b || busy} onClick={run}>Compare</Button>
      </div>
      {err && <div className="mt-3 text-sm text-bad">{err}</div>}
      {res && (
        <div className="mt-4">
          <div className="mb-3 text-sm text-fg-2">
            {res.identical_files ? "The two files are identical. " : ""}
            <b>{res.same_pct}%</b> of the pages are the same
            {Object.entries(res.counts).filter(([k, n]: any) => k !== "same" && n > 0).map(([k, n]: any) => ` · ${n} ${STATE[k].label}`).join("")}
          </div>
          <div className="overflow-hidden rounded-xl border border-line">
            <table className="w-full text-[13px]">
              <thead className="bg-subtle text-left text-xs text-fg-3">
                <tr><th className="px-3 py-2 font-medium">{res.a.name}</th><th className="px-3 py-2 font-medium">Result</th><th className="px-3 py-2 font-medium">{res.b.name}</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {res.rows.map((r: any, i: number) => (
                  <tr key={i}>
                    <td className="px-3 py-1.5 tnum">{r.a_page ? `page ${r.a_page}` : <span className="text-fg-4">—</span>}</td>
                    <td className="px-3 py-1.5">
                      <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs ${STATE[r.state].cls}`}>
                        {STATE[r.state].label}{r.state === "edited" && r.similarity != null ? ` · ${r.similarity}% alike` : ""}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 tnum">{r.b_page ? `page ${r.b_page}` : <span className="text-fg-4">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Family({ fam }: { fam: any }) {
  const base = fam.versions[0];
  return (
    <div className="px-5 py-4">
      <div className="mb-3 flex flex-wrap items-baseline gap-3 text-sm">
        <span className="font-medium text-fg">{fam.doc_no || base.name}</span>
        <span className="text-[13px] text-fg-3">
          {fam.versions.length} versions · {fam.unique_slides} unique slides of {fam.slide_occurrences} · {fam.stored_once_saving_pct}% stored once
        </span>
      </div>
      <div className="space-y-2.5">
        {fam.versions.map((v: any, i: number) => {
          const changed = v.slides.filter((s: any) => s.state === "changed").map((s: any) => s.page);
          return (
            <div key={v.id} className="flex flex-wrap items-center gap-3">
              <div className="w-56 min-w-0">
                <Link to={`/transcripts/${v.id}`} className="block truncate text-[13px] font-medium text-fg hover:text-accent-700">{v.name}</Link>
                <div className="text-xs text-fg-3">{v.rev ? `rev ${v.rev} · ` : ""}{v.system}</div>
              </div>
              <div className="flex flex-wrap gap-[2px]" role="img" aria-label={`${v.slides.length - changed.length} slides unchanged, changed: ${changed.join(", ") || "none"}`}>
                {v.slides.map((s: any) => (
                  <span
                    key={s.page}
                    title={`Slide ${s.page}: ${i === 0 ? "first version" : s.state === "same" ? "same as in the first version" : "new or changed"}`}
                    className={`flex h-6 w-6 items-center justify-center rounded text-[10px] tnum ${i > 0 && s.state === "changed" ? "bg-accent-600 font-semibold text-on-accent" : "bg-subtle text-fg-3 ring-1 ring-inset ring-line"}`}
                  >
                    {s.page}
                  </span>
                ))}
              </div>
              <div className="text-xs text-fg-3">
                {i === 0 ? "first version" : `${v.slides.length - changed.length} of ${v.slides.length} unchanged${changed.length ? ` · changed: ${changed.join(", ")}` : ""}${v.removed?.length ? ` · removed: ${v.removed.join(", ")}` : ""}`}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
