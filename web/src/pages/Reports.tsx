import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Archive, Download, ExternalLink, Eye, FileText, Film, Folder, Link2, Loader2, ScrollText, X } from "lucide-react";
import { api, fmtBytes, fmtDate, fmtDuration } from "../api";
import { Badge, Button, EmptyState, Loading, PageHeader, usePoll } from "../ui";
import { Conf } from "./Upload";

/** The report shown on this page (a window over it) instead of a new browser tab: a tab that is blocked, or a viewer that is
 *  missing, would leave the View button doing nothing. Download and "open in a new tab" stay available. */
function ReportViewer({ x, onClose }: { x: any; onClose: () => void }) {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const url = `/api/report/${x.id}`;
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[rgba(16,24,40,0.75)] p-4" onClick={onClose}>
      <div className="mx-auto flex h-full w-full max-w-6xl flex-col overflow-hidden rounded-2xl bg-surface" onClick={(e) => e.stopPropagation()}>
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
          <div className="min-w-0 flex-1 truncate text-sm font-bold">{x.name}</div>
          <Button size="sm" variant="primary" icon={Download} href={url}>Download PDF</Button>
          <a href={`${url}?inline=1`} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-2 rounded-full border border-line px-3.5 text-[13px] font-bold hover:bg-hover">
            <ExternalLink className="h-4 w-4" /> New tab
          </a>
          <button onClick={onClose} className="rounded-md p-1.5 text-fg-3 hover:bg-hover" aria-label="Close"><X className="h-5 w-5" /></button>
        </div>
        <div className="relative min-h-0 flex-1 bg-subtle">
          {!loaded && (
            <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-fg-3">
              <Loader2 className="h-4 w-4 animate-spin" /> Preparing the report. Larger ones take a few seconds.
            </div>
          )}
          <iframe title={`Report: ${x.name}`} src={`${url}?inline=1`} className="h-full w-full" onLoad={() => setLoaded(true)} />
        </div>
      </div>
    </div>
  );
}

/** One PDF per file: summary, people, quality, storage, shared screens, transcript, proof and the JSON. */
export default function Reports() {
  const r = usePoll(() => api.get("/api/reports"), 0);
  const [viewing, setViewing] = useState<any>(null);
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {};
    (r.data || []).forEach((x: any) => (g[x.folder] ||= []).push(x));
    return g;
  }, [r.data]);
  if (!r.data) return <div className="px-8 py-7"><Loading /></div>;
  const folders = Object.keys(groups).sort((a, b) => a.localeCompare(b));
  return (
    <div>
      <PageHeader
        title="Reports"
        description="One PDF per file, the final proof: summary, people, recognition quality, storage saved, shared screens with their images, the full transcript, the fingerprint of the original and what happened to it, and the JSON (printed and attached inside the PDF)."
      />
      {!folders.length && (
        <div className="panel"><EmptyState icon={ScrollText} title="No reports yet" description="Reports are available once a recording or document is processed." /></div>
      )}
      <div className="space-y-6">
        {folders.map((name) => (
          <section key={name} className="panel overflow-hidden">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
              <h2 className="flex items-center gap-2 text-base font-bold"><Folder className="h-4 w-4 text-fg-3" />{name}</h2>
              <Button size="sm" icon={Archive} href={`/api/folders/${encodeURIComponent(name)}/reports`}>All reports (ZIP)</Button>
            </header>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th">File</th>
                    <th className="th">Content</th>
                    <th className="th text-right">Original</th>
                    <th className="th text-right">Kept</th>
                    <th className="th text-right">Smaller</th>
                    <th className="th w-[230px]" />
                  </tr>
                </thead>
                <tbody>
                  {groups[name].map((x) => {
                    const Icon = x.shortcut ? Link2 : x.kind === "video" ? Film : FileText;
                    return (
                      <tr key={x.id}>
                        <td className="td">
                          <div className="flex items-center gap-2.5">
                            <Icon className="h-4 w-4 shrink-0 text-fg-3" />
                            <div className="min-w-0">
                              <Link to={`/transcripts/${x.id}`} className="block truncate font-medium text-fg hover:text-accent-700">{x.name}</Link>
                              <div className="text-xs text-fg-3">
                                {x.shortcut ? "shortcut: report of the original" : `processed ${fmtDate(x.processed_at)}`}
                                {x.original_deleted ? " · original deleted" : ""}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="td tnum text-[13px]">
                          {x.kind === "video" ? (
                            <span>{fmtDuration(x.duration_s)} · {x.speakers ?? 0} speaker{x.speakers === 1 ? "" : "s"} · <Conf v={x.confidence} /></span>
                          ) : (
                            <span>{x.pages} slides</span>
                          )}
                        </td>
                        <td className={`td tnum text-right text-[13px] ${x.original_deleted ? "text-fg-4 line-through" : ""}`}>{fmtBytes(x.original)}</td>
                        <td className="td tnum text-right text-[13px]">{x.kept != null ? fmtBytes(x.kept) : "—"}</td>
                        <td className="td tnum text-right text-[13px] font-medium text-ok">{x.reduction != null ? `${(100 * x.reduction).toFixed(1)}%` : "—"}</td>
                        <td className="td">
                          <div className="flex justify-end gap-2">
                            <Button size="sm" icon={Eye} onClick={() => setViewing(x)}>View</Button>
                            <Button size="sm" variant="primary" icon={Download} href={`/api/report/${x.id}`}>PDF</Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        ))}
      </div>
      {viewing && <ReportViewer x={viewing} onClose={() => setViewing(null)} />}
      <p className="mt-4 flex items-center gap-1.5 text-xs text-fg-3">
        <Badge>JSON inside</Badge> The PDF carries transcript.json (and transcript.md) as attachments: open the paper-clip / attachments panel of your PDF reader.
      </p>
    </div>
  );
}
