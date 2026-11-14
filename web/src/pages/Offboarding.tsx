import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ClipboardCheck, FileText, Film, Loader2, LogIn, Play, RotateCcw, ShieldCheck, Trash2, UserMinus, XCircle } from "lucide-react";
import { api, fmtBytes, fmtDate } from "../api";
import { Badge, Button, EmptyState, Metric, Panel, PageHeader, Progress, Tabs, usePoll, useUI } from "../ui";

const ACTION: Record<string, [string, string]> = {
  distill_then_remove: ["amber", "Distil, then move video"],
  distilled_already: ["green", "Already distilled"],
  copy_and_keep: ["blue", "Read and keep"],
  skip: ["neutral", "Skipped"],
  ignore: ["neutral", "Ignored"],
};
const RESULT: Record<string, [string, string]> = {
  quarantined: ["green", "In quarantine"],
  recycled: ["green", "In recycle bin"],
  kept: ["neutral", "Kept"],
};
const QSTATE: Record<string, [string, string]> = {
  quarantined: ["amber", "Quarantined"],
  deleted: ["neutral", "Deleted (recycle bin)"],
  recycled: ["neutral", "Recycle bin"],
  restored: ["green", "Restored"],
};

export default function Offboarding() {
  const [tab, setTab] = useState("offboard");
  const status = usePoll(() => api.get("/api/m365/status"), 4000);
  const signedIn = !!status.data?.token?.signed_in;
  return (
    <div>
      <PageHeader
        title="Employee offboarding"
        description="Through Microsoft 365: read a leaver's OneDrive, keep the distilled form of their meeting recordings (transcript, key frames, voice samples), and only then move the cloud videos aside. Every deletion is checked, reversible for a while, and written to a tamper-evident log."
      />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Tabs
          tabs={[{ id: "offboard", label: "Offboard" }, { id: "quarantine", label: "Quarantine" }, { id: "audit", label: "Audit log" }, { id: "connection", label: "Connection" }]}
          value={tab}
          onChange={setTab}
        />
        {status.data && (
          <Badge tone={signedIn ? "green" : "amber"} dot title={signedIn ? "Signed in" : "Not signed in"}>
            {status.data.mode === "mock" ? "Mock tenant" : "Microsoft 365"} · {signedIn ? "signed in" : "not signed in"}
          </Badge>
        )}
      </div>
      {tab === "connection" && <Connection status={status} />}
      {tab === "quarantine" && <Quarantine />}
      {tab === "audit" && <Audit />}
      {tab === "offboard" && (signedIn ? <Offboard mode={status.data.mode} /> : <NotSignedIn onGo={() => setTab("connection")} />)}
    </div>
  );
}

function NotSignedIn({ onGo }: { onGo: () => void }) {
  return (
    <Panel bodyClass="p-0">
      <EmptyState
        icon={LogIn}
        title="Sign in to Microsoft 365 first"
        description="There is no real tenant connected in this project: the mock tenant behaves like one (same API, same sign-in) so the whole flow can be tried safely."
        action={<Button variant="primary" onClick={onGo}>Open connection</Button>}
      />
    </Panel>
  );
}

// ------------------------------------------------------------------ connection
function Connection({ status }: { status: any }) {
  const { toast } = useUI();
  const s = status.data;
  const [mode, setMode] = useState("mock");
  const [tenant, setTenant] = useState("");
  const [clientId, setClientId] = useState("");
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState<any>(null);
  useEffect(() => {
    if (s) { setMode(s.mode); setTenant(s.tenant || ""); setClientId(s.client_id || ""); }
  }, [s?.mode]);

  async function save() {
    await api.post("/api/m365/connect", { mode, tenant, client_id: clientId });
    toast("Connection saved");
    status.reload();
  }
  async function signIn() {
    setBusy(true);
    try {
      const r = await api.post("/api/m365/signin", {});
      setCode(r);
      toast("Signed in");
      status.reload();
    } catch (e: any) {
      toast(e.message, "bad");
    } finally {
      setBusy(false);
    }
  }
  async function seed() {
    const r = await api.post("/api/m365/mock/seed", {});
    toast(`Mock tenant filled: ${r.files.length} files in ${r.user}'s OneDrive`);
    status.reload();
  }
  if (!s) return null;
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Panel title="Tenant" description="Which Microsoft 365 the offboarding talks to">
        <div className="space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-fg-2">Mode</span>
            <select className="field w-full" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="mock">Mock tenant (local, for development and demos)</option>
              <option value="real">Real Microsoft 365 tenant</option>
            </select>
          </label>
          {mode === "real" && (
            <>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-fg-2">Tenant ID</span>
                <input className="field w-full" value={tenant} onChange={(e) => setTenant(e.target.value)} placeholder="contoso.onmicrosoft.com" />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-fg-2">Application (client) ID</span>
                <input className="field w-full" value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="from the Azure app registration" />
              </label>
              <p className="text-xs text-fg-3">
                A tenant administrator registers the app (public client, device code flow) and grants: {s.scopes_needed.join(", ")}. Nothing is typed into DataDistill but the IDs; you sign in at microsoft.com.
              </p>
            </>
          )}
          <div className="flex flex-wrap gap-2">
            <Button onClick={save}>Save</Button>
            <Button variant="primary" icon={busy ? Loader2 : LogIn} onClick={signIn} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
            {s.token.signed_in && <Button onClick={async () => { await api.post("/api/m365/signout", {}); status.reload(); }}>Sign out</Button>}
          </div>
          {code && <p className="text-xs text-fg-3">{code.message || `Code ${code.code}`}</p>}
        </div>
      </Panel>
      {s.mode === "mock" && (
        <Panel title="Mock tenant" description="A local stand-in with the same API as Microsoft Graph">
          <p className="mb-3 text-sm text-fg-2">
            Fill it with a leaver (<span className="font-medium">alex.leaver@contoso.example</span>) whose OneDrive holds the public test recordings and decks, including a recording shared with an anonymous link and one under a retention label, which the safety checks must skip.
          </p>
          <Button icon={FileText} onClick={seed} disabled={!s.token.signed_in && false}>Fill with demo data</Button>
        </Panel>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ offboarding flow
function Offboard({ mode }: { mode: string }) {
  const { toast, confirm } = useUI();
  const users = usePoll(() => api.get("/api/m365/users"), 0);
  const plans = usePoll(() => api.get("/api/m365/plans"), 2000);
  const [upn, setUpn] = useState("");
  const [opts, setOpts] = useState<any>({ mode: "quarantine", quarantine_days: 14, max_items: 100, include_shared: false, require_second_approver: false });
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [approver, setApprover] = useState("");
  const [second, setSecond] = useState("");
  const [typed, setTyped] = useState("");
  const active = plans.data?.active?.[0];
  const list: any[] = users.data?.users || [];

  useEffect(() => { if (!upn && list.length) setUpn(list[0].userPrincipalName); }, [users.data]);

  // when the run ends, load the finished plan with its report
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (active) setSeen(true);
    else if (seen && plan) { setSeen(false); api.get(`/api/m365/plans/${plan.id}`).then(setPlan); }
  }, [active?.id]);

  async function check() {
    setBusy(true);
    setPlan(null);
    try {
      setPlan(await api.post("/api/m365/plan", { upn, options: opts }));
      setTyped(""); setApprover(""); setSecond("");
    } catch (e: any) {
      toast(e.message, "bad");
    } finally {
      setBusy(false);
    }
  }
  async function approve() {
    try {
      setPlan(await api.post(`/api/m365/plans/${plan.id}/approve`, { approver, typed_upn: typed, second_approver: second }));
      toast("Plan approved");
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }
  async function run() {
    const s = plan.summary;
    const ok = await confirm({
      title: `Run the approved plan for ${plan.user_upn}?`,
      body: (
        <div className="space-y-2">
          <p>{s.to_distil} recording{s.to_distil === 1 ? "" : "s"} will be downloaded, checked against the cloud's hash, distilled and only then {plan.options.mode === "quarantine" ? `moved to a quarantine folder (deleted after ${plan.options.quarantine_days} days)` : "moved to the recycle bin"}.</p>
          <p>Anything that fails a check is left untouched. The cloud space is freed only after the recycle bin retention.</p>
        </div>
      ),
      confirm: "Run",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.post(`/api/m365/plans/${plan.id}/run`, {});
      toast("Started");
      plans.reload();
    } catch (e: any) {
      toast(e.message, "bad");
    }
  }

  const s = plan?.summary;
  return (
    <div className="space-y-6">
      <section className="panel space-y-4 px-5 py-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-[260px] flex-1">
            <span className="mb-1.5 block text-sm font-medium text-fg-2">Leaver</span>
            <select className="field w-full" value={upn} onChange={(e) => { setUpn(e.target.value); setPlan(null); }} disabled={!!active}>
              {list.map((u) => <option key={u.id} value={u.userPrincipalName}>{u.displayName} ({u.userPrincipalName})</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-sm font-medium text-fg-2">Cloud videos</span>
            <select className="field" value={opts.mode} onChange={(e) => setOpts({ ...opts, mode: e.target.value })}>
              <option value="quarantine">Quarantine first, delete later</option>
              <option value="recycle_bin">Move to recycle bin now</option>
            </select>
          </label>
          {opts.mode === "quarantine" && (
            <label>
              <span className="mb-1.5 block text-sm font-medium text-fg-2">Days</span>
              <input className="field w-20" type="number" min={1} value={opts.quarantine_days} onChange={(e) => setOpts({ ...opts, quarantine_days: +e.target.value })} />
            </label>
          )}
          <Button icon={busy ? Loader2 : ClipboardCheck} onClick={check} disabled={!upn || busy || !!active}>{busy ? "Reading OneDrive…" : "Check (dry run)"}</Button>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-fg-2">
          <label className="flex items-center gap-2"><input type="checkbox" checked={opts.include_shared} onChange={(e) => setOpts({ ...opts, include_shared: e.target.checked })} /> Include files shared outside the organisation</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={opts.require_second_approver} onChange={(e) => setOpts({ ...opts, require_second_approver: e.target.checked })} /> Require a second approver</label>
          <label className="flex items-center gap-2">At most <input className="field w-24 py-1" type="number" min={1} value={opts.max_items} onChange={(e) => setOpts({ ...opts, max_items: +e.target.value })} /> videos per run</label>
        </div>
      </section>

      {active && <Active a={active} />}

      {plan && !active && (
        <>
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            <Metric label="Files read" value={s.files} hint={`${s.videos} recording${s.videos === 1 ? "" : "s"}, ${s.documents} document${s.documents === 1 ? "" : "s"}`} />
            <Metric label="To distil and move" value={s.to_distil + s.already_distilled} hint={fmtBytes(s.bytes_to_remove)} />
            <Metric label="Skipped by a check" value={s.skipped} tone={s.skipped ? "warn" : undefined} hint="left untouched" />
            <Metric label="Status" value={plan.status} hint={plan.approved_by ? `approved by ${plan.approved_by}` : "nothing changed yet"} />
          </div>
          <Panel title={`Plan for ${plan.user_upn}`} description="A dry run: nothing in the cloud or the library has changed. Open a row to see its checks." bodyClass="">
            <PlanTable rows={plan.items} report={plan.report} />
          </Panel>
          {plan.status === "planned" && (
            <Panel title="Approve" description="The approval is tied to exactly this plan. If anything in the cloud changes before the run, the file is skipped and you plan again.">
              <div className="grid gap-3 md:grid-cols-3">
                <label><span className="mb-1.5 block text-sm font-medium text-fg-2">Your name</span><input className="field w-full" value={approver} onChange={(e) => setApprover(e.target.value)} /></label>
                {plan.options.require_second_approver && (
                  <label><span className="mb-1.5 block text-sm font-medium text-fg-2">Second approver (a different person)</span><input className="field w-full" value={second} onChange={(e) => setSecond(e.target.value)} /></label>
                )}
                <label><span className="mb-1.5 block text-sm font-medium text-fg-2">Type the leaver's address to confirm</span><input className="field w-full" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={plan.user_upn} /></label>
              </div>
              <div className="mt-4"><Button variant="primary" icon={ShieldCheck} onClick={approve} disabled={!s.to_distil && !s.already_distilled && !s.documents}>Approve this plan</Button></div>
            </Panel>
          )}
          {plan.status === "approved" && (
            <div className="flex items-center gap-3"><Button variant="primary" icon={Play} onClick={run}>Run the approved plan</Button><span className="text-sm text-fg-3">approved by {plan.approved_by}{plan.second_approver ? ` and ${plan.second_approver}` : ""}</span></div>
          )}
          {plan.status === "done" && plan.report && (
            <div className="rounded-xl border border-line bg-subtle px-4 py-3 text-sm text-fg-2">
              <span className="font-medium text-fg">Finished.</span> {plan.report.summary.quarantined + plan.report.summary.recycled} moved ({fmtBytes(plan.report.summary.bytes_moved)}), {plan.report.summary.kept} kept. {plan.report.note}
            </div>
          )}
        </>
      )}

      {!plan && !active && (
        <Panel title="Earlier plans" bodyClass={plans.data?.plans?.length ? "" : "p-0"}>
          {!plans.data?.plans?.length ? (
            <EmptyState icon={UserMinus} title="No offboarding plans yet" description="Choose the leaver and check their OneDrive. Nothing changes until you approve and run a plan." />
          ) : (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-fg-3"><th className="px-5 py-2">Leaver</th><th>Date</th><th>Videos</th><th>Status</th><th /></tr></thead>
              <tbody>
                {plans.data.plans.map((p: any) => (
                  <tr key={p.id} className="border-t border-line">
                    <td className="px-5 py-2.5">{p.user_upn}</td>
                    <td>{fmtDate(p.created_at)}</td>
                    <td>{p.summary.videos}</td>
                    <td><Badge tone={p.status === "done" ? "green" : "neutral"}>{p.status}</Badge></td>
                    <td className="pr-5 text-right"><Button size="sm" onClick={async () => setPlan(await api.get(`/api/m365/plans/${p.id}`))}>Open</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      )}
    </div>
  );
}

function Active({ a }: { a: any }) {
  return (
    <Panel>
      <div className="mb-2 text-base font-bold">Offboarding is running</div>
      <div className="mb-1 text-sm text-fg-2">{a.stage}</div>
      <Progress value={a.progress || 0} />
      <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-subtle p-3 text-xs text-fg-3">{(a.log || []).join("\n")}</pre>
    </Panel>
  );
}

function PlanTable({ rows, report }: { rows: any[]; report?: any }) {
  const [open, setOpen] = useState<string | null>(null);
  const res: Record<string, any> = Object.fromEntries((report?.files || []).map((r: any) => [r.item_id, r]));
  return (
    <table className="w-full text-sm">
      <thead><tr className="text-left text-xs text-fg-3"><th className="px-5 py-2">File</th><th>Size</th><th>Plan</th>{report && <th>Result</th>}</tr></thead>
      <tbody>
        {rows.filter((r) => r.kind !== "other").map((r) => {
          const [tone, label] = ACTION[r.action] || ["neutral", r.action];
          const out = res[r.item_id];
          return (
            <>
              <tr key={r.item_id} className="cursor-pointer border-t border-line hover:bg-subtle" onClick={() => setOpen(open === r.item_id ? null : r.item_id)}>
                <td className="px-5 py-2.5"><span className="flex items-center gap-2">{r.kind === "video" ? <Film size={15} className="text-fg-3" /> : <FileText size={15} className="text-fg-3" />}<span className="font-medium text-fg">{r.name}</span><span className="text-xs text-fg-4">{r.path}</span></span></td>
                <td className="tnum">{fmtBytes(r.size)}</td>
                <td><Badge tone={tone}>{label}</Badge></td>
                {report && <td>{out ? <Badge tone={(RESULT[out.result] || ["neutral"])[0]}>{(RESULT[out.result] || ["", out.result])[1]}</Badge> : null}</td>}
              </tr>
              {open === r.item_id && (
                <tr key={r.item_id + "x"} className="bg-subtle">
                  <td colSpan={report ? 4 : 3} className="px-5 py-3">
                    <div className="mb-1 text-xs text-fg-3">{r.reason}</div>
                    {r.checks?.map((c: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        {c.ok ? <CheckCircle2 size={13} className="text-ok" /> : <XCircle size={13} className="text-bad" />}
                        <span className={c.ok ? "text-fg-2" : "font-medium text-fg"}>{c.check}</span>
                        {c.detail && <span className="text-fg-4">{c.detail}</span>}
                      </div>
                    ))}
                    {out?.steps?.map((t: string, i: number) => <div key={i} className="text-xs text-fg-3">→ {t}</div>)}
                    {out?.note && <div className="mt-1 flex items-center gap-1 text-xs text-warn"><AlertTriangle size={13} />{out.note}</div>}
                  </td>
                </tr>
              )}
            </>
          );
        })}
      </tbody>
    </table>
  );
}

// ------------------------------------------------------------------ quarantine
function Quarantine() {
  const { toast, confirm } = useUI();
  const q = usePoll(() => api.get("/api/m365/quarantine"), 3000);
  const items: any[] = q.data?.items || [];
  async function restore(i: any) {
    try { await api.post(`/api/m365/quarantine/${i.id}/restore`, {}); toast(`${i.name} restored`); q.reload(); } catch (e: any) { toast(e.message, "bad"); }
  }
  async function del(i: any) {
    const ok = await confirm({ title: `Delete ${i.name} now?`, body: "It moves to the cloud recycle bin before its quarantine time is up. The file is checked once more first.", confirm: "Delete", danger: true });
    if (!ok) return;
    try { const r = await api.post(`/api/m365/quarantine/${i.id}/delete`, { typed: i.name }); toast(r.deleted.length ? "Deleted (recycle bin)" : r.kept[0]?.reason || "Refused", r.deleted.length ? undefined : "bad"); q.reload(); } catch (e: any) { toast(e.message, "bad"); }
  }
  async function due() {
    const r = await api.post("/api/m365/quarantine/finalize-due", {});
    toast(`${r.deleted.length} deleted, ${r.kept.length} refused`);
    q.reload();
  }
  return (
    <Panel title="Quarantine" description="Cloud videos that were distilled and moved aside. They are deleted (to the recycle bin) only when their time is up and a final check passes." actions={<Button icon={Trash2} onClick={due}>Delete what is due</Button>} bodyClass={items.length ? "" : "p-0"}>
      {!items.length ? (
        <EmptyState icon={ShieldCheck} title="Nothing in quarantine" description="Videos appear here after an approved offboarding run." />
      ) : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-fg-3"><th className="px-5 py-2">Video</th><th>Size</th><th>State</th><th>Deleted after</th><th /></tr></thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id} className="border-t border-line">
                <td className="px-5 py-2.5"><div className="font-medium text-fg">{i.name}</div><div className="text-xs text-fg-4">{i.path}{i.note ? ` · ${i.note}` : ""}</div></td>
                <td className="tnum">{fmtBytes(i.size)}</td>
                <td><Badge tone={(QSTATE[i.state] || ["neutral"])[0]}>{(QSTATE[i.state] || ["", i.state])[1]}</Badge></td>
                <td className="tnum">{i.state === "quarantined" ? `${i.days_left} days (${fmtDate(i.delete_after)})` : "-"}</td>
                <td className="whitespace-nowrap pr-5 text-right">
                  {["quarantined", "recycled", "deleted"].includes(i.state) && <Button size="sm" icon={RotateCcw} onClick={() => restore(i)}>Restore</Button>}{" "}
                  {i.state === "quarantined" && <Button size="sm" icon={Trash2} onClick={() => del(i)}>Delete now</Button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ audit
function Audit() {
  const a = usePoll(() => api.get("/api/m365/audit"), 4000);
  const v = a.data?.verify;
  return (
    <Panel
      title="Audit log"
      description="Every step is chained to the one before by a hash. If an entry is edited or removed, the chain breaks and this page says so."
      actions={v && <Badge tone={v.ok ? "green" : "red"} dot>{v.ok ? `Chain intact (${v.entries} entries)` : `Chain broken at entry ${v.broken_at}`}</Badge>}
      bodyClass={a.data?.entries?.length ? "" : "p-0"}
    >
      {!a.data?.entries?.length ? (
        <EmptyState icon={ShieldCheck} title="No entries yet" />
      ) : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-fg-3"><th className="px-5 py-2">Time</th><th>Action</th><th>Details</th></tr></thead>
          <tbody>
            {a.data.entries.map((e: any) => (
              <tr key={e.id} className="border-t border-line align-top">
                <td className="whitespace-nowrap px-5 py-2 tnum">{fmtDate(e.ts)}</td>
                <td className="pr-4 font-medium">{e.action.replace("m365.", "")}</td>
                <td className="py-2 pr-5 text-xs text-fg-3">{Object.entries(e.details).filter(([k]) => !["prev", "hash"].includes(k)).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
