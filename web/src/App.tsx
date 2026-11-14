import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api } from "./api";
import { usePoll } from "./ui";
import Overview from "./pages/Overview";
import Upload from "./pages/Upload";
import Transcripts from "./pages/Transcripts";
import Search from "./pages/Search";
import Offboarding from "./pages/Offboarding";
import Storage from "./pages/Storage";
import Duplicates from "./pages/Duplicates";
import Reports from "./pages/Reports";

const nav = [
  { to: "/overview", label: "Overview" },
  { to: "/upload", label: "Upload" },
  { to: "/transcripts", label: "Meetings" },
  { to: "/search", label: "Search" },
  { to: "/duplicates", label: "Duplicates" },
  { to: "/offboarding", label: "Offboarding" },
  { to: "/reports", label: "Reports" },
  { to: "/storage", label: "Storage" },
];

export function Logo({ size = 40 }: { size?: number }) {
  return (
    <span className="inline-flex shrink-0 items-center justify-center rounded-xl bg-accent-500 text-on-accent" style={{ width: size, height: size }}>
      <svg viewBox="0 0 24 24" width={size * 0.55} height={size * 0.55} fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" aria-hidden="true">
        <path d="M5 7h14" />
        <path d="M7 12h10" />
        <path d="M10 17h4" />
      </svg>
    </span>
  );
}

export default function App() {
  const { data: stats } = usePoll(() => api.get("/api/stats"), 3000);
  const busy = stats?.active_jobs > 0;
  return (
    <div className="min-h-full p-3 sm:p-5">
      <div className="mx-auto flex min-h-[calc(100vh-40px)] max-w-[1440px] flex-col rounded-[28px] bg-shell px-4 pb-8 pt-4 sm:px-6 sm:pt-5">
        <header className="mb-7 flex flex-wrap items-center justify-between gap-3">
          <Link to="/overview" className="flex items-center gap-2.5 text-fg" aria-label="DataDistill home">
            <Logo />
            <span className="text-lg font-extrabold tracking-[-0.01em]">DataDistill</span>
          </Link>
          <nav aria-label="Main" className="order-3 flex w-full gap-0.5 overflow-x-auto rounded-full bg-surface p-[5px] [scrollbar-width:none] 2xl:order-none 2xl:w-auto">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  `inline-flex h-10 shrink-0 items-center rounded-full px-4 text-sm font-semibold whitespace-nowrap transition ${
                    isActive ? "bg-ink text-on-ink" : "text-fg-2 hover:bg-hover hover:text-fg"
                  }`
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          {busy && (
            <Link to="/upload" className="inline-flex h-11 items-center gap-2 rounded-full bg-accent-50 px-4 text-[13px] font-bold text-accent-700">
              <Loader2 className="h-4 w-4 animate-spin" />
              {stats.active_jobs} processing
            </Link>
          )}
        </header>
        <main className="min-w-0 flex-1">
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Overview />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/transcripts" element={<Transcripts />} />
            <Route path="/transcripts/:id" element={<Transcripts />} />
            <Route path="/search" element={<Search />} />
            <Route path="/offboarding" element={<Offboarding />} />
            <Route path="/storage" element={<Storage />} />
            <Route path="/duplicates" element={<Duplicates />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
