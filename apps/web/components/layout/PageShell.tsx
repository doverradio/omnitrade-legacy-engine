import Link from "next/link";
import type { ReactNode } from "react";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/decision-arena", label: "Decision Arena" },
  { href: "/decision-intelligence", label: "Decision Intelligence" },
  { href: "/markets", label: "Markets" },
  { href: "/strategy-lab", label: "Strategy Lab" },
  { href: "/backtests", label: "Backtests" },
  { href: "/signals", label: "Signals" },
  { href: "/paper-trading", label: "Portfolio Intelligence" },
  { href: "/live-trading", label: "Live Trading Ops" },
  { href: "/risk-monitor", label: "Risk Monitor" },
  { href: "/settings", label: "Settings" },
];

type PageShellProps = {
  children: ReactNode;
};

export default function PageShell({ children }: PageShellProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="flex min-h-screen flex-col md:flex-row">
        <aside className="hidden w-64 border-r border-border bg-muted p-4 md:block" aria-label="Primary">
          <p className="mb-4 text-sm font-semibold uppercase tracking-wide text-foreground/80">
            OmniTrade
          </p>
          <p className="mb-3 text-xs uppercase tracking-wide text-foreground/60">
            Phase 8 decision arena
          </p>
          <nav className="flex flex-col gap-2 text-sm">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="rounded-md px-3 py-2 text-foreground/90 transition hover:bg-foreground/10"
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </aside>

        <div className="flex min-h-screen flex-1 flex-col">
          <header className="border-b border-border px-4 py-3 sm:px-6">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm">Decision Arena & Decision Intelligence (read-only)</p>
              <p className="text-xs uppercase tracking-wide text-foreground/70">
                Paper mode only - observational surfaces
              </p>
            </div>

            <nav className="mt-3 flex gap-2 overflow-x-auto pb-1 md:hidden" aria-label="Mobile primary">
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="whitespace-nowrap rounded-md border border-border bg-background/40 px-3 py-1.5 text-xs font-medium text-foreground/90"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </header>
          <main className="flex-1 p-4 sm:p-6">{children}</main>
        </div>
      </div>
    </div>
  );
}
