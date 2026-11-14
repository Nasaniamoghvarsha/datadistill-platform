// Thin fetch wrapper around the FastAPI backend (proxied by Vite in dev, same origin in production).

async function req<T = any>(method: string, url: string, body?: any): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as any)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = j.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export const api = {
  get: <T = any>(u: string) => req<T>("GET", u),
  post: <T = any>(u: string, b?: any) => req<T>("POST", u, b ?? {}),
  patch: <T = any>(u: string, b?: any) => req<T>("PATCH", u, b),
  put: <T = any>(u: string, b?: any) => req<T>("PUT", u, b),
  del: <T = any>(u: string) => req<T>("DELETE", u),
};

export const out = (p?: string | null) => (p ? `/out/${p}` : "");

export function fmtBytes(b?: number | null) {
  if (b == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}

/** 1:05 / 1:02:05 */
export function fmtTime(t?: number | null) {
  const s = Math.max(0, Math.floor(t || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(ss).padStart(2, "0")}`;
}

/** 00:01:05 */
export function fmtClock(t?: number | null) {
  const s = Math.max(0, Math.floor(t || 0));
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((n) => String(n).padStart(2, "0")).join(":");
}

export function fmtDuration(t?: number | null) {
  const s = Math.round(t || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min`;
  return `${s} s`;
}

export function fmtDate(ts?: number | null) {
  return ts ? new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}
