import Link from "next/link";
import type { ReactNode } from "react";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/markets", label: "Markets" },
  { href: "/strategy-lab", label: "Strategy Lab" },
  { href: "/backtests", label: "Backtests" },
  { href: "/signals", label: "Signals" },
  { href: "/paper-trading", label: "Paper Trading" },
  { href: "/risk-monitor", label: "Risk Monitor" },
  { href: "/settings", label: "Settings" },
];

type PageShellProps = {
  children: ReactNode;
};

export default function PageShell({ children }: PageShellProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="flex min-h-screen">
        <aside className="w-64 border-r border-border bg-muted p-4">
          <p className="mb-4 text-sm font-semibold uppercase tracking-wide text-foreground/80">
            OmniTrade
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
          <header className="flex h-14 items-center justify-between border-b border-border px-6">
            <p className="text-sm">Top bar placeholder</p>
            <p className="text-xs uppercase tracking-wide text-foreground/70">Paper trading</p>
          </header>
          <main className="flex-1 p-6">{children}</main>
        </div>
      </div>
    </div>
  );
}
