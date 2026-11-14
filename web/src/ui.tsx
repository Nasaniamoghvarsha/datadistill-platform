import { createPortal } from "react-dom";
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Building2, CheckCircle2, Info, Loader2, LucideIcon, MoreHorizontal, User, X } from "lucide-react";

/* ------------------------------------------------------------------ data hook */
export function usePoll<T = any>(fn: () => Promise<T>, ms: number, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    let timer: any;
    const run = async () => {
      try {
        const d = await fnRef.current();
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e: any) {
        if (alive) setError(e.message);
      }
      if (alive && ms > 0) timer = setTimeout(run, ms);
    };
    run();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1), setData };
}

/* ------------------------------------------------------------------ buttons */
type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
const btnBase =
  "inline-flex items-center justify-center gap-2 rounded-full font-bold whitespace-nowrap transition focus-visible:outline-none focus-visible:ring-4 disabled:pointer-events-none disabled:opacity-50";
const btnVariants: Record<BtnVariant, string> = {
  primary: "bg-accent-600 text-on-accent hover:brightness-110 focus-visible:ring-accent-100",
  secondary: "border border-line bg-surface text-fg hover:bg-hover focus-visible:ring-accent-100",
  ghost: "text-fg-3 hover:bg-hover hover:text-fg",
  danger: "border border-bad/40 bg-surface text-bad hover:bg-bad-bg focus-visible:ring-bad-bg",
};
const btnSizes = { sm: "h-9 px-3.5 text-[13px]", md: "h-11 px-[18px] text-sm", icon: "h-9 w-9" };

/** File name from a Content-Disposition header (the UTF-8 form first, then the plain one). */
function fileNameOf(res: Response, fallback: string) {
  const h = res.headers.get("Content-Disposition") || "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(h);
  if (star) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(h);
  return plain ? plain[1] : fallback;
}

/** A download button: the file is made on the server first (a report or a zip with audio can take several seconds), so the button
 *  says it is working, and a failure is shown instead of nothing happening. The file is then saved from here. */
export function DownloadButton({ href, cls, Icon, children, title, compact, ...rest }: any) {
  const [busy, setBusy] = useState(false);
  const { toast } = useUI();
  async function go(e: React.MouseEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const res = await fetch(href);
      if (!res.ok) {
        let msg = `${res.status} ${res.statusText}`;
        try {
          msg = (await res.json()).detail || msg;
        } catch {
          /* not JSON */
        }
        throw new Error(msg);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileNameOf(res, "download");
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err: any) {
      toast(`Download failed: ${err.message}`, "bad");
    }
    setBusy(false);
  }
  return (
    <a href={href} onClick={go} className={`${cls} ${busy ? "pointer-events-none opacity-70" : ""}`} aria-busy={busy} title={busy ? "Preparing the file. Larger ones take a few seconds." : title} {...rest}>
      {busy ? <Loader2 className="h-4 w-4 shrink-0 animate-spin" strokeWidth={2} /> : Icon && <Icon className="h-4 w-4 shrink-0" strokeWidth={2} />}
      {busy ? (compact ? null : "Preparing…") : children}
    </a>
  );
}

export function Button({ variant = "secondary", size = "md", icon: Icon, children, href, download, className = "", ...rest }: any) {
  const cls = `${btnBase} ${btnVariants[variant as BtnVariant]} ${btnSizes[size as keyof typeof btnSizes]} ${className}`;
  const inner = (
    <>
      {Icon && <Icon className="h-4 w-4 shrink-0" strokeWidth={2} />}
      {children}
    </>
  );
  if (href && href.startsWith("/api/") && size !== "icon")
    return (
      <DownloadButton href={href} cls={cls} Icon={Icon} {...rest}>
        {children}
      </DownloadButton>
    );
  if (href && href.startsWith("/") && !href.startsWith("/api/"))
    return (
      <Link to={href} className={cls} {...rest}>
        {inner}
      </Link>
    );
  if (href)
    return (
      <a href={href} download={download} className={cls} {...rest}>
        {inner}
      </a>
    );
  return (
    <button className={cls} {...rest}>
      {inner}
    </button>
  );
}

/* ------------------------------------------------------------------ layout */
export function PageHeader({ title, description, actions, back }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; back?: ReactNode }) {
  return (
    <div className="mb-6">
      {back}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-[30px] font-extrabold leading-9 tracking-[-0.02em] text-fg">{title}</h1>
          {description && <div className="mt-1.5 max-w-3xl text-[15px] text-fg-3">{description}</div>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

export function Panel({ title, description, actions, children, className = "", bodyClass = "p-5" }: any) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-[22px] py-4">
          <div>
            <h2 className="text-base font-bold leading-6 text-fg">{title}</h2>
            {description && <p className="mt-0.5 text-[13px] text-fg-3">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Metric({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "ok" | "warn" }) {
  const c = tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-fg";
  return (
    <div className="panel px-[22px] py-5">
      <div className="text-[13px] font-semibold text-fg-3">{label}</div>
      <div className={`tnum mt-2 text-[28px] font-extrabold leading-8 tracking-[-0.02em] ${c}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-fg-3">{hint}</div>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string; count?: number }[]; value: string; onChange: (v: string) => void }) {
  return (
    <div role="tablist" className="mb-5 inline-flex max-w-full gap-0.5 overflow-x-auto rounded-full bg-surface p-[5px]">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={`flex h-9 shrink-0 items-center gap-2 rounded-full px-4 text-sm font-semibold whitespace-nowrap transition ${
            value === t.id ? "bg-ink text-on-ink" : "text-fg-2 hover:bg-hover hover:text-fg"
          }`}
        >
          {t.label}
          {t.count != null && (
            <span className={`tnum rounded-full px-2 text-xs font-bold leading-5 ${value === t.id ? "bg-on-ink/20 text-on-ink" : "bg-subtle text-fg-3"}`}>{t.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, description, action }: { icon: LucideIcon; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-50 text-accent-700">
        <Icon className="h-5 w-5" />
      </div>
      <div className="text-base font-bold text-fg">{title}</div>
      {description && <div className="mt-1 max-w-sm text-sm text-fg-3">{description}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ badges */
const tones: Record<string, string> = {
  neutral: "bg-subtle text-fg-2",
  blue: "bg-accent-50 text-accent-700",
  green: "bg-ok-bg text-ok",
  amber: "bg-warn-bg text-warn",
  red: "bg-bad-bg text-bad",
  purple: "bg-ai-bg text-ai",
};

export function Badge({ tone = "neutral", dot, children, title }: { tone?: string; dot?: boolean; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 text-xs font-bold ${tones[tone] || tones.neutral}`}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

const statusMap: Record<string, [string, string]> = {
  new: ["neutral", "Not processed"],
  queued: ["blue", "Queued"],
  processing: ["blue", "Processing"],
  processed: ["green", "Ready"],
  error: ["red", "Failed"],
  quarantined: ["amber", "Quarantined"],
  removed: ["neutral", "Removed"],
  shortcut: ["blue", "Shortcut"],
  duplicate: ["purple", "Same recording"],
};
export function StatusBadge({ status }: { status: string }) {
  const [tone, label] = statusMap[status] || ["neutral", status];
  return (
    <Badge tone={tone} dot>
      {label}
    </Badge>
  );
}

export const verdictMeta: Record<string, [string, string]> = {
  SAFE_DELETE: ["green", "Safe to delete"],
  REVIEW: ["amber", "Review"],
  ARCHIVE_DISTILLED: ["blue", "Archive original"],
  KEEP: ["neutral", "Keep"],
  NOT_ANALYSED: ["neutral", "Not analysed"],
};

export const isPersonal = (s: string) => /^(onedrive|personal|home)/i.test(s);
export function SystemTag({ system }: { system: string }) {
  const Icon = isPersonal(system) ? User : Building2;
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-fg-3" title={isPersonal(system) ? "Personal drive" : "Shared system"}>
      <Icon className="h-3.5 w-3.5" />
      {system}
    </span>
  );
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-line">
      <div className="h-full rounded-full bg-accent-500 transition-all duration-500" style={{ width: `${Math.round((value || 0) * 100)}%` }} />
    </div>
  );
}

/* ------------------------------------------------------------------ row menu */
export function RowMenu({ items }: { items: ({ label: string; icon?: LucideIcon; onClick: () => void; danger?: boolean; hidden?: boolean } | null)[] }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top?: number; bottom?: number; right: number }>({ right: 0 });
  const ref = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const visible = items.filter((i): i is NonNullable<typeof i> => !!i && !i.hidden);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !menuRef.current?.contains(t)) setOpen(false);
    };
    const shut = () => setOpen(false);
    document.addEventListener("mousedown", close);
    window.addEventListener("scroll", shut, true);
    window.addEventListener("resize", shut);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", shut, true);
      window.removeEventListener("resize", shut);
    };
  }, [open]);
  function toggle() {
    if (open) return setOpen(false);
    const r = ref.current!.getBoundingClientRect();
    const need = visible.length * 42 + 20;                       // the menu may be taller than the space below the button
    const right = Math.max(8, window.innerWidth - r.right);
    setPos(window.innerHeight - r.bottom < need + 8 ? { bottom: window.innerHeight - r.top + 4, right } : { top: r.bottom + 4, right });
    setOpen(true);
  }
  return (
    <div ref={ref} className="relative inline-block" onClick={(e) => e.stopPropagation()}>
      <Button variant="ghost" size="icon" aria-label="Actions" onClick={toggle} icon={MoreHorizontal} />
      {open &&
        // drawn on top of the page, not inside the table panel: a panel with rounded, clipped edges cut the menu off
        createPortal(
          <div
            ref={menuRef}
            style={{ position: "fixed", zIndex: 70, ...pos }}
            className="shadow-pop w-52 overflow-hidden rounded-2xl border border-line bg-surface p-1.5"
            onClick={(e) => e.stopPropagation()}
          >
            {visible.map((i) => (
              <button
                key={i.label}
                onClick={() => {
                  setOpen(false);
                  i.onClick();
                }}
                className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm font-semibold hover:bg-hover ${i.danger ? "text-bad" : "text-fg-2"}`}
              >
                {i.icon && <i.icon className="h-4 w-4" />}
                {i.label}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}

/* ------------------------------------------------------------------ dialogs + toasts */
type ConfirmOpts = {
  title: string;
  body?: ReactNode;
  confirm?: string;
  danger?: boolean;
  input?: { label: string; value: string; options?: string[] };
};
type Toast = { id: number; tone: "ok" | "bad" | "info"; text: string };
const UICtx = createContext<{ confirm: (o: ConfirmOpts) => Promise<string | false>; toast: (text: string, tone?: Toast["tone"]) => void }>(null as any);
export const useUI = () => useContext(UICtx);

export function UIProvider({ children }: { children: ReactNode }) {
  const [dlg, setDlg] = useState<(ConfirmOpts & { resolve: (v: string | false) => void }) | null>(null);
  const [val, setVal] = useState("");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const confirm = useCallback(
    (o: ConfirmOpts) =>
      new Promise<string | false>((resolve) => {
        setVal(o.input?.value || "");
        setDlg({ ...o, resolve });
      }),
    [],
  );
  const toast = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  const close = (v: string | false) => {
    dlg?.resolve(v);
    setDlg(null);
  };
  return (
    <UICtx.Provider value={{ confirm, toast }}>
      {children}
      {dlg && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4" onMouseDown={() => close(false)}>
          <div className="shadow-pop w-full max-w-md rounded-[24px] bg-surface p-6" onMouseDown={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-extrabold text-fg">{dlg.title}</h3>
            {dlg.body && <div className="mt-2 text-sm text-fg-3">{dlg.body}</div>}
            {dlg.input && (
              <label className="mt-4 block">
                <span className="mb-1.5 block text-sm font-medium text-fg-2">{dlg.input.label}</span>
                {dlg.input.options ? (
                  <select autoFocus className="field w-full" value={val} onChange={(e) => setVal(e.target.value)}>
                    {dlg.input.options.map((o) => (
                      <option key={o} value={o}>{o}</option>
                    ))}
                  </select>
                ) : (
                  <input autoFocus className="field w-full" value={val} onChange={(e) => setVal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && close(val || " ")} />
                )}
              </label>
            )}
            <div className="mt-6 flex justify-end gap-2">
              <Button onClick={() => close(false)}>Cancel</Button>
              <Button variant={dlg.danger ? "danger" : "primary"} onClick={() => close(val || "ok")}>
                {dlg.confirm || "Confirm"}
              </Button>
            </div>
          </div>
        </div>
      )}
      <div className="fixed bottom-5 right-5 z-50 space-y-2">
        {toasts.map((t) => {
          const Icon = t.tone === "ok" ? CheckCircle2 : t.tone === "bad" ? AlertCircle : Info;
          return (
            <div key={t.id} className="shadow-pop flex w-96 max-w-[calc(100vw-40px)] items-start gap-3 rounded-2xl border border-line bg-surface px-4 py-3">
              <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${t.tone === "ok" ? "text-ok" : t.tone === "bad" ? "text-bad" : "text-accent-600"}`} />
              <div className="flex-1 text-sm text-fg-2">{t.text}</div>
              <button onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))} className="text-fg-4 hover:text-fg-3">
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    </UICtx.Provider>
  );
}

/* ------------------------------------------------------------------ modal (custom content) */
export function Modal({ title, description, children, footer, onClose, wide }: { title: ReactNode; description?: ReactNode; children?: ReactNode; footer?: ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4" onMouseDown={onClose}>
      <div className={`shadow-pop flex max-h-[90vh] w-full ${wide ? "max-w-3xl" : "max-w-xl"} flex-col rounded-[24px] bg-surface`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 px-6 pb-2 pt-6">
          <div>
            <h3 className="text-lg font-extrabold text-fg">{title}</h3>
            {description && <div className="mt-1 text-sm text-fg-3">{description}</div>}
          </div>
          <button onClick={onClose} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line text-fg-3 hover:bg-hover hover:text-fg" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-3">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-6 py-4">{footer}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ misc */
const STOP = new Set("the and for are was were what where when who how why does did this that these those with from have has into about there their them then than come comes".split(" "));
export function Highlight({ text, query }: { text: string; query: string }) {
  const words = query.toLowerCase().split(/\W+/).filter((w) => w.length > 2 && !STOP.has(w));
  if (!words.length) return <>{text}</>;
  const alt = words.map((w) => `\\b${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\w{0,3}\\b`).join("|");
  const re = new RegExp(`(${alt})`, "gi");
  const isHit = new RegExp(`^(${alt})$`, "i");
  return (
    <>
      {text.split(re).map((p, i) =>
        isHit.test(p) ? (
          <mark key={i} className="rounded-sm bg-accent-100 px-0.5 text-fg">
            {p}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

const speakerTones = ["text-accent-700", "text-warn", "text-ok", "text-ai", "text-bad", "text-fg-2"];
export function speakerTone(label: string | null, all: string[]) {
  if (!label) return "text-fg-3";
  return speakerTones[Math.max(0, all.indexOf(label)) % speakerTones.length];
}

export function ErrorNote({ error }: { error?: string | null }) {
  if (!error) return null;
  return (
    <div className="mb-5 flex items-center gap-2 rounded-2xl bg-bad-bg px-4 py-3 text-sm font-semibold text-bad">
      <AlertCircle className="h-4 w-4" /> {error}
    </div>
  );
}

export function Loading() {
  return <div className="py-16 text-center text-sm text-fg-3">Loading…</div>;
}
