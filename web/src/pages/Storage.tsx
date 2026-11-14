import { useState } from "react";
import { Link } from "react-router-dom";
import { Database } from "lucide-react";
import { api, fmtBytes, fmtDuration } from "../api";
import { EmptyState, Loading, Metric, PageHeader, Panel, usePoll, useUI } from "../ui";

// validated categorical slots 1-3 (dataviz reference palette): distilled, originals, saved
const C = { distilled: "#2a78d6", originals: "#eb6834", saved: "#1baf7a" };
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
const pct = (a: number, b: number) => (b ? `${(100 * a / b).toFixed(1)}%` : "—");

export default function Storage() {
  const s = usePoll(() => api.get("/api/storage"), 5000);
  if (!s.data) return <div className="px-8 py-7"><Loading /></div>;
  const t = s.data.totals;
  if (!t.ingested)
    return (
      <div>
        <PageHeader title="Storage" />
        <div className="panel"><EmptyState icon={Database} title="Nothing stored yet" description="Upload recordings to see how much storage is saved." /></div>
      </div>
    );
  return (
    <div>
      <PageHeader title="Storage" description="What was brought in, what is stored now, and where the savings come from." />

      <div className="mb-6 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Metric label="Brought in" value={fmtBytes(t.ingested)} hint={[plural(t.recordings, "recording"), plural(t.documents, "document"), plural(t.shortcut_count, "shortcut")].join(" · ")} />
        <Metric label="Stored now" value={fmtBytes(t.stored)} hint={`${fmtBytes(t.originals_on_disk)} originals · ${fmtBytes(t.distilled)} distilled`} />
        <Metric label="Saved" value={fmtBytes(t.saved)} tone="ok" hint={`${pct(t.saved, t.ingested)} of everything brought in`} />
        <Metric label="Transcribed" value={fmtDuration(t.hours * 3600)} hint={`${t.words.toLocaleString()} words · ${t.snapshots} snapshots`} />
      </div>

      <Panel title="Where the storage goes" description="Everything brought in, split by what happened to it" className="mb-6">
        <Composition t={t} />
      </Panel>

      <div className="mb-6 grid gap-6 xl:grid-cols-2">
        <Panel title="Savings by source" bodyClass="px-5 py-2">
          <dl className="tnum divide-y divide-line text-[13px]">
            <Line k={`Duplicates replaced by shortcuts (${t.shortcut_count})`} v={fmtBytes(t.shortcut_bytes)} />
            <Line k={`Videos deleted after distillation (${t.deleted_count})`} v={fmtBytes(t.deleted_bytes)} />
            <Line k="Kept from those videos (transcripts, snapshots, voices)" v={fmtBytes(t.distilled)} />
            <div className="flex justify-between py-2 font-medium"><dt>Saved</dt><dd className="text-ok">{fmtBytes(t.saved)}</dd></div>
          </dl>
          {t.potential_count > 0 && (
            <p className="border-t border-line py-3 text-[13px] text-fg-3">
              {t.potential_count} transcribed recording{t.potential_count === 1 ? " still has its" : "s still have their"} original video: another{" "}
              <span className="font-medium text-fg">{fmtBytes(t.potential_bytes)}</span> can be freed with an <Link className="link" to="/offboarding">offboarding run</Link>.
            </p>
          )}
        </Panel>
        <Panel title="Distilled output" bodyClass="px-5 py-2">
          <dl className="tnum divide-y divide-line text-[13px]">
            <Line k="Transcripts (JSON + text)" v={fmtBytes(t.transcript_bytes)} />
            <Line k={`Key frames and snapshots (${t.snapshots} moments)`} v={fmtBytes(t.snapshot_bytes)} />
            <Line k="Voice samples" v={fmtBytes(t.voice_bytes)} />
            <Line k="Per hour of meeting" v={t.hours ? fmtBytes((t.transcript_bytes + t.snapshot_bytes + t.voice_bytes) / t.hours) : "—"} />
          </dl>
          <VoiceSetting reload={s.reload} hasVoices={t.voice_bytes > 0} />
          <SpeakerSettings />
        </Panel>
      </div>

      <Scenarios t={t} />

      <Panel title="Recordings" description="Original video vs. what is kept" className="mb-6" bodyClass="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              <th className="th">Recording</th>
              <th className="th text-right">Length</th>
              <th className="th text-right">Original</th>
              <th className="th text-right">Transcript</th>
              <th className="th text-right">Snapshots</th>
              <th className="th text-right">Voices</th>
              <th className="th text-right">Kept</th>
              <th className="th text-right">Reduction</th>
            </tr>
          </thead>
          <tbody>
            {s.data.recordings.map((r: any) => (
              <tr key={r.id}>
                <td className="td">
                  <Link to={`/transcripts/${r.id}`} className="block truncate font-medium text-fg hover:text-accent-700">{r.name}</Link>
                  <div className="text-xs text-fg-3">{r.folder}{r.deleted ? " · original deleted" : ""}</div>
                </td>
                <td className="td tnum text-right text-[13px]">{fmtDuration(r.duration_s)}</td>
                <td className={`td tnum text-right text-[13px] ${r.deleted ? "text-fg-4 line-through" : ""}`}>{fmtBytes(r.original)}</td>
                <td className="td tnum text-right text-[13px]">{fmtBytes(r.transcript)}</td>
                <td className="td tnum text-right text-[13px]">{fmtBytes(r.snapshots)}{r.snapshot_count ? <span className="text-fg-3"> ({r.snapshot_count})</span> : null}</td>
                <td className="td tnum text-right text-[13px]">{fmtBytes(r.voices)}</td>
                <td className="td tnum text-right text-[13px] font-medium">{fmtBytes(r.distilled)}</td>
                <td className="td tnum text-right text-[13px] font-medium text-ok">{r.reduction != null ? `${(100 * r.reduction).toFixed(1)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title="Folders" bodyClass="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              <th className="th">Folder</th>
              <th className="th text-right">Files</th>
              <th className="th text-right">Shortcuts</th>
              <th className="th text-right">Videos deleted</th>
              <th className="th text-right">Brought in</th>
              <th className="th text-right">Stored now</th>
              <th className="th text-right">Saved</th>
            </tr>
          </thead>
          <tbody>
            {s.data.folders.map((f: any) => (
              <tr key={f.folder}>
                <td className="td font-medium text-fg">{f.folder}</td>
                <td className="td tnum text-right text-[13px]">{f.files}</td>
                <td className="td tnum text-right text-[13px]">{f.shortcuts}</td>
                <td className="td tnum text-right text-[13px]">{f.deleted}</td>
                <td className="td tnum text-right text-[13px]">{fmtBytes(f.ingested)}</td>
                <td className="td tnum text-right text-[13px]">{fmtBytes(f.stored)}</td>
                <td className="td tnum text-right text-[13px] font-medium text-ok">{fmtBytes(f.saved)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

/** How speakers are found in the picture: both can be switched off (cameras show people; overlap marking changes the transcript). */
function SpeakerSettings() {
  const { toast } = useUI();
  const st = usePoll(() => api.get("/api/settings"), 0);
  async function flip(k: string, label: string) {
    await api.patch("/api/settings", { [k]: !st.data?.[k] });
    st.reload();
    toast(`${label}: ${st.data?.[k] ? "off" : "on"} for new recordings`);
  }
  if (!st.data) return null;
  const row = (k: string, title: string, text: string) => (
    <label className="flex cursor-pointer items-start gap-2.5 py-1.5">
      <input type="checkbox" className="mt-0.5 h-4 w-4 accent-accent-500" checked={!!st.data[k]} onChange={() => flip(k, title)} />
      <span>
        <span className="font-medium text-fg">{title}</span>
        <span className="block text-xs text-fg-3">{text}</span>
      </span>
    </label>
  );
  return (
    <div className="border-t border-line py-3 text-[13px]">
      {row("camera_analysis", "Read mouth movement of camera tiles", "Who is speaking, seen in the video. Only a number per tile and second is kept: no face image or description, no recognition of people.")}
      {row("mark_overlap", "Mark people speaking at the same time", "Lines where two name labels are lit or two mouths move at once get a note. The text of such lines may be incomplete: the speech model writes mostly the louder voice.")}
    </div>
  );
}

/** Voice samples are personal data (a voice identifies a person): they can be switched off and deleted. */
function VoiceSetting({ reload, hasVoices }: { reload: () => void; hasVoices: boolean }) {
  const { confirm, toast } = useUI();
  const st = usePoll(() => api.get("/api/settings"), 0);
  const on = !!st.data?.keep_voice_samples;
  async function toggle() {
    await api.patch("/api/settings", { keep_voice_samples: !on });
    st.reload();
    toast(on ? "New recordings get no voice samples" : "New recordings get a voice sample per speaker");
  }
  async function wipe() {
    if (!(await confirm({ title: "Delete all voice samples?", body: "Every stored voice sample is deleted. Transcripts, key frames and snapshots stay. This cannot be undone.", confirm: "Delete voice samples", danger: true }))) return;
    const r = await api.post("/api/voices/delete-all");
    toast(`${r.files} voice samples deleted (${fmtBytes(r.bytes)})`);
    reload();
  }
  if (!st.data) return null;
  return (
    <div className="border-t border-line py-3 text-[13px]">
      <label className="flex cursor-pointer items-start gap-2.5">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-accent-500" checked={on} onChange={toggle} />
        <span>
          <span className="font-medium text-fg">Keep a voice sample per speaker</span>
          <span className="block text-xs text-fg-3">About 10 s of each voice. A voice identifies a person (personal data under GDPR), so this can be switched off.</span>
        </span>
      </label>
      {hasVoices && <button onClick={wipe} className="mt-2 text-xs font-medium text-bad hover:underline">Delete all stored voice samples</button>}
    </div>
  );
}

type Sc = { name: string; note: string; rec: number; hours: number; mbph: number; decks: number; mbpd: number; dup: number };

/** P3: scenario-based savings. Inputs are editable; "kept per hour" and the duplicate share come from what was measured here. */
function Scenarios({ t }: { t: any }) {
  const MB = 1e6;
  const keptPerHour = t.hours > 0 ? (t.transcript_bytes + t.snapshot_bytes + t.voice_bytes) / t.hours : 0;
  const textPerHour = t.hours > 0 ? t.transcript_bytes / t.hours : 0;
  const measuredDup = t.ingested ? t.shortcut_bytes / t.ingested : 0;
  const [rows, setRows] = useState<Sc[]>(() => [
    { name: "One SharePoint site", note: "200 meetings of 1 h, 2,000 decks", rec: 200, hours: 1, mbph: 500, decks: 2000, mbpd: 4, dup: 20 },
    { name: "One leaver's OneDrive", note: "40 meetings, 300 decks, many copies", rec: 40, hours: 1, mbph: 500, decks: 300, mbpd: 4, dup: 35 },
    { name: "Company-wide, per year", note: "10,000 meetings of 1 h", rec: 10000, hours: 1, mbph: 500, decks: 50000, mbpd: 4, dup: 20 },
  ]);
  const set = (i: number, k: keyof Sc, v: number) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const calc = (r: Sc) => {
    const before = r.rec * r.hours * r.mbph * MB + r.decks * r.mbpd * MB;
    const after = (1 - r.dup / 100) * (r.rec * r.hours * keptPerHour + r.decks * r.mbpd * MB);
    return { before, after, saved: before - after, pct: before ? 1 - after / before : 0 };
  };
  const tiers = [
    { k: "T0 Original video", v: 500 * MB, note: "the brief: 2 h ≈ 1 GB" },
    { k: "T1 Archive", v: keptPerHour + 7.2 * MB, note: "kept + full audio (Opus 16 kbit/s)" },
    { k: "T2 Knowledge (what DataDistill keeps)", v: keptPerHour, note: "transcript, key frames, snapshots, voice samples" },
    { k: "T3 Text only", v: textPerHour, note: "transcript with slide ids" },
  ];
  const num = (i: number, k: keyof Sc, step = 1) => (
    <input type="number" min={0} step={step} value={rows[i][k] as number} onChange={(e) => set(i, k, Math.max(0, Number(e.target.value)))}
      className="field tnum h-8 w-20 px-2 text-right text-[13px]" />
  );
  return (
    <Panel title="Savings scenarios" description={`Estimates. Measured here: ${fmtBytes(keptPerHour)} kept per hour of meeting${measuredDup ? `, ${(100 * measuredDup).toFixed(1)}% of the content were copies` : ""}. Edit any number.`}
      className="mb-6" bodyClass="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr>
            <th className="th">Scenario</th>
            <th className="th text-right">Meetings</th>
            <th className="th text-right">Hours each</th>
            <th className="th text-right">MB per hour</th>
            <th className="th text-right">Decks</th>
            <th className="th text-right">MB per deck</th>
            <th className="th text-right">Copies %</th>
            <th className="th text-right">Before</th>
            <th className="th text-right">After</th>
            <th className="th text-right">Saved</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const c = calc(r);
            return (
              <tr key={r.name}>
                <td className="td min-w-[190px]">
                  <div className="font-medium text-fg">{r.name}</div>
                  <div className="text-xs text-fg-3">{r.note}</div>
                </td>
                <td className="td text-right">{num(i, "rec")}</td>
                <td className="td text-right">{num(i, "hours", 0.5)}</td>
                <td className="td text-right">{num(i, "mbph", 50)}</td>
                <td className="td text-right">{num(i, "decks", 100)}</td>
                <td className="td text-right">{num(i, "mbpd", 0.5)}</td>
                <td className="td text-right">{num(i, "dup")}</td>
                <td className="td tnum text-right text-[13px]">{fmtBytes(c.before)}</td>
                <td className="td tnum text-right text-[13px]">{fmtBytes(c.after)}</td>
                <td className="td tnum text-right text-[13px] font-medium text-ok">{(100 * c.pct).toFixed(1)}%</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="border-t border-line px-5 py-3">
        <div className="mb-2 text-[13px] font-medium text-fg">Retention tiers, per hour of meeting</div>
        <dl className="tnum grid gap-x-8 gap-y-1 text-[13px] sm:grid-cols-2">
          {tiers.map((x) => (
            <div key={x.k} className="flex justify-between gap-3">
              <dt className="text-fg-3" title={x.note}>{x.k}</dt>
              <dd className="text-fg">{fmtBytes(x.v)} <span className="text-fg-3">({(100 * (1 - x.v / tiers[0].v)).toFixed(2)}% less)</span></dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-fg-3">Real savings are likely higher: SharePoint version history and recycle bins also count against the quota.</p>
      </div>
    </Panel>
  );
}

function Line({ k, v }: { k: string; v: any }) {
  return (
    <div className="flex justify-between gap-3 py-2">
      <dt className="text-fg-3">{k}</dt>
      <dd className="text-right text-fg">{v}</dd>
    </div>
  );
}

/** One horizontal bar = everything brought in: originals still on disk | distilled output | saved. */
function Composition({ t }: { t: any }) {
  const [hover, setHover] = useState<string | null>(null);
  const parts = [
    { key: "originals", label: "Originals on disk", v: t.originals_on_disk, color: C.originals },
    { key: "distilled", label: "Distilled output", v: t.distilled, color: C.distilled },
    { key: "saved", label: "Saved", v: t.saved, color: C.saved },
  ];
  const total = parts.reduce((a, p) => a + p.v, 0) || 1;
  return (
    <div>
      <div className="flex h-8 w-full gap-[2px]" role="img" aria-label={parts.map((p) => `${p.label} ${fmtBytes(p.v)}`).join(", ")}>
        {parts
          .filter((p) => p.v > 0)
          .map((p, i, arr) => (
            <div
              key={p.key}
              onMouseEnter={() => setHover(p.key)}
              onMouseLeave={() => setHover(null)}
              className={`relative h-full transition-opacity ${hover && hover !== p.key ? "opacity-60" : ""} ${i === 0 ? "rounded-l" : ""} ${i === arr.length - 1 ? "rounded-r" : ""}`}
              style={{ width: `max(${(100 * p.v) / total}%, 3px)`, background: p.color }}
            >
              {hover === p.key && (
                <div className="tnum pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-fg shadow-lg">
                  <span className="font-medium">{p.label}</span> · {fmtBytes(p.v)} · {pct(p.v, total)}
                </div>
              )}
            </div>
          ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-2">
        {parts.map((p) => (
          <div key={p.key} className="flex items-center gap-2 text-[13px]" onMouseEnter={() => setHover(p.key)} onMouseLeave={() => setHover(null)}>
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: p.color }} />
            <span className="text-fg-3">{p.label}</span>
            <span className="tnum font-medium text-fg">{fmtBytes(p.v)}</span>
            <span className="tnum text-fg-3">{pct(p.v, total)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
