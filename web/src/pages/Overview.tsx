import { useState } from "react";
import { Link } from "react-router-dom";
import { Captions, FileText, Folder, Layers, Link2, Mic, Upload as UploadIcon, UserMinus, Video } from "lucide-react";
import { api, fmtBytes, fmtDate, fmtDuration } from "../api";
import { Badge, Button, ErrorNote, Loading, StatusBadge, usePoll } from "../ui";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const pct = (x: number) => (x >= 0.9995 ? "99.9" : (100 * x).toFixed(1));

export default function Overview() {
  const storage = usePoll(() => api.get("/api/storage"), 8000);
  const files = usePoll<any[]>(() => api.get("/api/files"), 5000);
  const [filter, setFilter] = useState<"all" | "video" | "document">("all");

  if (!storage.data || !files.data) return storage.error || files.error ? <ErrorNote error={storage.error || files.error} /> : <Loading />;
  const t = storage.data.totals;
  const recs: any[] = storage.data.recordings || [];
  const all = files.data;
  const videos = all.filter((f) => f.kind === "video" && f.status !== "shortcut");
  const done = videos.filter((f) => f.meta?.confidence != null);
  const seconds = done.reduce((s, f) => s + (f.meta?.duration || 0), 0);
  const conf = done.length ? done.reduce((s, f) => s + f.meta.confidence, 0) / done.length : null;
  const voices = done.reduce((s, f) => s + (f.meta?.speakers || 0), 0);
  const recent = [...all]
    .filter((f) => filter === "all" || f.kind === filter)
    .sort((a, b) => (b.processed_at || b.added_at || 0) - (a.processed_at || a.added_at || 0))
    .slice(0, 6);

  // the story of the recordings: what they were, what is kept, what is already freed, what can be freed now
  const orig = recs.reduce((s, r) => s + r.original, 0);
  const kept = recs.reduce((s, r) => s + r.distilled, 0);
  const reduction = orig > 0 ? 1 - kept / orig : 0;
  const freedOriginals = recs.filter((r) => r.deleted).reduce((s, r) => s + r.original, 0);
  const readyBytes = t.potential_bytes || 0;
  const seg = [
    { key: "freed", label: "Freed so far", bytes: freedOriginals, color: "#9ff0c8" },
    { key: "ready", label: "Ready to free", bytes: readyBytes, color: "rgba(255,255,255,0.45)" },
  ];
  const parts = [
    { label: "Transcripts", bytes: t.transcript_bytes || 0, color: "var(--c-accent-500)" },
    { label: "Slide pictures", bytes: t.snapshot_bytes || 0, color: "var(--c-ok-bar)" },
    { label: "Voice samples", bytes: t.voice_bytes || 0, color: "var(--c-ai)" },
  ];
  const partsTotal = Math.max(1, parts.reduce((s, p) => s + p.bytes, 0));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[34px] font-extrabold leading-10 tracking-[-0.02em]">{greeting()}</h1>
          <p className="mt-1.5 text-[15px] text-fg-3">
            {done.length} meeting{done.length === 1 ? "" : "s"} distilled, {t.documents} document{t.documents === 1 ? "" : "s"} read, and nothing left this laptop.
          </p>
        </div>
        <Button variant="primary" icon={UploadIcon} href="/upload">Upload data</Button>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.25fr_1fr]">
        {/* ---------- the storage story */}
        <section className="hero flex flex-col gap-5 rounded-[22px] p-[26px]">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] font-bold text-white/85">Storage, once recordings are distilled</span>
            <span className="rounded-full bg-white/15 px-2.5 py-1 text-xs font-bold">{recs.length} recording{recs.length === 1 ? "" : "s"}</span>
          </div>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div className="tnum text-[64px] font-extrabold leading-none tracking-[-0.03em]">{orig > 0 ? `${pct(reduction)}%` : "—"}</div>
            <div className="pb-1.5 text-[15px] leading-5 text-white/90">
              smaller per recording
              <div className="text-[13px] text-white/75">{fmtBytes(orig)} of video kept as {fmtBytes(kept)} of searchable knowledge</div>
            </div>
          </div>

          {/* where the bytes of the recordings are: freed / ready to free / kept */}
          <div>
            <div className="flex h-3.5 w-full overflow-hidden rounded-full bg-white/15">
              {seg.map((s) => (
                <div key={s.key} style={{ width: `${(100 * s.bytes) / Math.max(1, orig)}%`, background: s.color, minWidth: s.bytes > 0 ? 6 : 0 }} title={`${s.label}: ${fmtBytes(s.bytes)}`} />
              ))}
            </div>
            <div className="mt-3 grid grid-cols-3 gap-3 text-[13px]">
              <div>
                <div className="flex items-center gap-1.5 text-white/80"><span className="h-2.5 w-2.5 rounded-full" style={{ background: "#9ff0c8" }} />Freed so far</div>
                <div className="tnum mt-0.5 text-[22px] font-extrabold leading-6">{fmtBytes(t.saved)}</div>
                <div className="text-white/70">net, after keeping the knowledge</div>
              </div>
              <div>
                <div className="flex items-center gap-1.5 text-white/80"><span className="h-2.5 w-2.5 rounded-full bg-white/45" />Ready to free</div>
                <div className="tnum mt-0.5 text-[22px] font-extrabold leading-6">{fmtBytes(readyBytes)}</div>
                <div className="text-white/70">{t.potential_count || 0} distilled recording{t.potential_count === 1 ? "" : "s"}, original still on disk</div>
              </div>
              <div>
                <div className="flex items-center gap-1.5 text-white/80"><span className="h-2.5 w-2.5 rounded-full border border-white/70" />Kept as knowledge</div>
                <div className="tnum mt-0.5 text-[22px] font-extrabold leading-6">{fmtBytes(t.distilled)}</div>
                <div className="text-white/70">transcripts, slide pictures, voices</div>
              </div>
            </div>
          </div>

          <div className="mt-auto flex gap-2.5">
            <Link to="/upload" className="flex h-11 flex-1 items-center justify-center rounded-full bg-white text-sm font-bold text-[#0057b8] hover:bg-white/90">Upload</Link>
            <Link to="/offboarding" className="flex h-11 flex-1 items-center justify-center gap-2 rounded-full bg-white/15 text-sm font-bold text-white hover:bg-white/25">
              <UserMinus className="h-4 w-4" />
              Offboard a leaver
            </Link>
          </div>
        </section>

        {/* ---------- before and after, recording by recording */}
        <section className="panel flex flex-col gap-4 p-[22px]">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-bold">Before and after</h2>
            <Link to="/storage" className="link text-[13px]">Storage</Link>
          </div>
          <div className="flex items-center gap-4 text-xs font-semibold text-fg-3">
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-line-strong" />Video</span>
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-ok-bar" />Kept</span>
          </div>
          <div className="flex flex-col gap-3.5">
            {[...recs].sort((a, b) => b.original - a.original).map((r) => (
              <Link key={r.id} to={`/transcripts/${r.id}`} className="group block" title={`${r.name}: ${fmtBytes(r.original)} became ${fmtBytes(r.distilled)}`}>
                <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
                  <span className="min-w-0 truncate font-semibold text-fg group-hover:text-accent-700">{r.name.replace(/\.[^.]+$/, "")}</span>
                  <span className="tnum shrink-0 font-bold text-ok">−{pct(r.reduction)}%</span>
                </div>
                <div className="h-2.5 w-full overflow-hidden rounded-full bg-line-strong/60">
                  <div className="h-full rounded-full bg-ok-bar" style={{ width: `${Math.max(1.2, 100 * (1 - r.reduction))}%` }} />
                </div>
                <div className="tnum mt-1 flex justify-between text-[11px] text-fg-3">
                  <span>{fmtBytes(r.original)}{r.deleted ? " · original deleted" : ""}</span>
                  <span>kept {fmtBytes(r.distilled)}</span>
                </div>
              </Link>
            ))}
            {!recs.length && <p className="py-6 text-center text-sm text-fg-3">Distilled recordings appear here.</p>}
          </div>
        </section>
      </div>

      {/* ---------- facts */}
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <Stat icon={Video} label="Meetings distilled" value={String(done.length)} hint={`${fmtDuration(seconds)} of recordings`} />
        <Stat icon={Captions} label="Searchable words" value={(t.words || 0).toLocaleString()} hint="with speaker, time and slide" />
        <Stat icon={Mic} label="Voices told apart" value={String(voices)} hint={`across ${done.length} recording${done.length === 1 ? "" : "s"}`} />
        <Stat icon={Layers} label="Speech confidence" value={conf == null ? "—" : `${Math.round(conf * 100)}%`} hint="the model's own certainty, not a measured accuracy" bar={conf ?? 0} />
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_1.4fr]">
        {/* ---------- what the kept knowledge is made of */}
        <section className="panel flex flex-col gap-4 p-[22px]">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-bold">What is kept</h2>
            <span className="tnum text-[13px] font-semibold text-fg-3">{fmtBytes(t.distilled)} in all</span>
          </div>
          <div className="flex items-center gap-6">
            <Donut parts={parts} total={partsTotal} center={fmtBytes(partsTotal)} />
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              {parts.map((p) => (
                <div key={p.label} className="flex items-center gap-3 text-sm">
                  <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: p.color }} />
                  <span className="flex-1 truncate text-fg-2">{p.label}</span>
                  <span className="tnum font-semibold text-fg">{fmtBytes(p.bytes)}</span>
                </div>
              ))}
              <p className="text-xs leading-5 text-fg-3">The rest of a recording, the pixels, is not needed to find what was said or shown.</p>
            </div>
          </div>
          {seconds > 0 && (
            <div className="mt-auto grid grid-cols-2 gap-3 border-t border-line pt-4">
              <div>
                <div className="text-xs font-semibold text-fg-3">One hour of meeting, as video</div>
                <div className="tnum mt-0.5 text-[22px] font-extrabold leading-6">{fmtBytes((orig / seconds) * 3600)}</div>
              </div>
              <div>
                <div className="text-xs font-semibold text-fg-3">One hour of meeting, kept</div>
                <div className="tnum mt-0.5 text-[22px] font-extrabold leading-6 text-ok">{fmtBytes((kept / seconds) * 3600)}</div>
              </div>
            </div>
          )}
        </section>

        <section className="panel flex flex-col overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 px-[22px] pb-3 pt-[22px]">
            <h2 className="text-base font-bold">Recent activity</h2>
            <div className="flex gap-1.5">
              {(["all", "video", "document"] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setFilter(k)}
                  className={`h-8 rounded-full border px-3.5 text-[13px] font-semibold transition ${filter === k ? "border-ink bg-ink text-on-ink" : "border-line bg-surface text-fg-2 hover:bg-hover"}`}
                >
                  {k === "all" ? "All" : k === "video" ? "Recordings" : "Documents"}
                </button>
              ))}
            </div>
          </div>
          <div className="divide-y divide-line border-t border-line">
            {recent.map((f) => (
              <Link key={f.id} to={`/transcripts/${f.id}`} className="flex items-center gap-3 px-[22px] py-3 hover:bg-hover">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${f.kind === "video" ? "bg-accent-50 text-accent-700" : "bg-ai-bg text-ai"}`}>
                  {f.status === "shortcut" ? <Link2 className="h-4 w-4" /> : f.kind === "video" ? <Video className="h-4 w-4" /> : <FileText className="h-4 w-4" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-bold text-fg">{f.name}</span>
                  <span className="flex items-center gap-1.5 truncate text-xs text-fg-3">
                    <Folder className="h-3 w-3 shrink-0" />
                    {f.folder} · {fmtDate(f.processed_at || f.added_at)}
                  </span>
                </span>
                <span className="hidden text-right text-xs font-semibold text-fg-3 sm:block">
                  {f.kind === "video" && f.meta?.duration ? fmtDuration(f.meta.duration) : f.meta?.pages ? `${f.meta.pages} pages` : ""}
                </span>
                {f.kind === "document" && f.status === "processed" ? <Badge tone="purple" dot>Document</Badge> : f.meta?.original_deleted ? <Badge tone="green" dot>Distilled</Badge> : <StatusBadge status={f.status} />}
              </Link>
            ))}
            {!recent.length && <p className="px-[22px] py-8 text-center text-sm text-fg-3">Nothing here yet.</p>}
          </div>
        </section>
      </div>
    </div>
  );
}

/** A small donut chart (plain SVG): each part is an arc of the ring. */
function Donut({ parts, total, center }: { parts: { label: string; bytes: number; color: string }[]; total: number; center: string }) {
  const R = 54;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="relative h-[148px] w-[148px] shrink-0">
      <svg viewBox="0 0 148 148" className="h-full w-full -rotate-90">
        <circle cx="74" cy="74" r={R} fill="none" strokeWidth="18" style={{ stroke: "var(--c-line)" }} />
        {parts.map((p) => {
          const len = (p.bytes / total) * C;
          const el = (
            <circle key={p.label} cx="74" cy="74" r={R} fill="none" strokeWidth="18" strokeLinecap="butt" style={{ stroke: p.color }}
              strokeDasharray={`${Math.max(0, len - 2)} ${C}`} strokeDashoffset={-offset}>
              <title>{`${p.label}: ${fmtBytes(p.bytes)}`}</title>
            </circle>
          );
          offset += len;
          return el;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tnum text-[22px] font-extrabold leading-6">{center}</span>
        <span className="text-[11px] font-semibold text-fg-3">kept</span>
      </div>
    </div>
  );
}

function Stat({ icon: Icon, label, value, hint, bar }: { icon: any; label: string; value: string; hint: string; bar?: number }) {
  return (
    <section className="panel flex flex-col gap-3 p-[22px]">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-semibold text-fg-3">{label}</span>
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-50 text-accent-700">
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div className="tnum text-[36px] font-extrabold leading-none tracking-[-0.02em]">{value}</div>
      <div className="text-[13px] text-fg-3">{hint}</div>
      {bar != null && (
        <div className="mt-auto h-2 overflow-hidden rounded-full bg-line">
          <div className="h-full rounded-full bg-ok-bar" style={{ width: `${Math.round(bar * 100)}%` }} />
        </div>
      )}
    </section>
  );
}
