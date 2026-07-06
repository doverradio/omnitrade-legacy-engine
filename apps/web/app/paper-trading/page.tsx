type ShellState = "loading" | "empty" | "error";

type ShellStateCardProps = {
  title: string;
  state: ShellState;
  loadingMessage: string;
  emptyMessage: string;
  errorMessage: string;
};

function ShellStateCard({
  title,
  state,
  loadingMessage,
  emptyMessage,
  errorMessage,
}: ShellStateCardProps) {
  let label = "Loading";
  let body = loadingMessage;

  if (state === "empty") {
    label = "Empty";
    body = emptyMessage;
  } else if (state === "error") {
    label = "Error";
    body = errorMessage;
  }

  return (
    <article className="rounded-lg border border-border bg-muted/40 p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">{title}</h3>
        <span className="rounded-full border border-border bg-background px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
          {label} state
        </span>
      </div>
      <p className="mt-3 text-sm text-foreground/75">{body}</p>
    </article>
  );
}

export default function PaperTradingPage() {
  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border bg-muted/60 p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">
          Phase 5 shell
        </p>
        <h1 className="mt-2 text-2xl font-semibold">Portfolio Intelligence + Paper Execution Foundation</h1>
        <p className="mt-2 max-w-3xl text-sm text-foreground/75">
          Paper-only validation workspace for portfolio behavior before any live-capital pathway.
          Small Account Mode is first-class here, with a $25 default proving ground and explicit paper
          balance language across all shell sections.
        </p>
      </section>

      <section className="rounded-lg border border-border bg-background p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">
          Section navigation
        </h2>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full border border-border bg-muted px-3 py-1">Paper account shell</span>
          <span className="rounded-full border border-border bg-muted px-3 py-1">
            Portfolio accounting shell
          </span>
          <span className="rounded-full border border-border bg-muted px-3 py-1">Trade timeline shell</span>
          <span className="rounded-full border border-border bg-muted px-3 py-1">Execution status shell</span>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <ShellStateCard
          title="Paper account shell"
          state="loading"
          loadingMessage="Loading paper account context for the default $25 proving-ground profile."
          emptyMessage="No paper account exists yet. Create a paper account with a starting balance of at least $25."
          errorMessage="Could not load paper account context. Retry without leaving the paper-only workflow."
        />

        <ShellStateCard
          title="Portfolio accounting shell"
          state="empty"
          loadingMessage="Loading paper balance, equity, and position placeholders."
          emptyMessage="No paper positions yet. Equity and P&L placeholders will populate after paper execution events."
          errorMessage="Portfolio accounting data could not be loaded. Verify paper account availability and retry."
        />

        <ShellStateCard
          title="Trade timeline shell"
          state="error"
          loadingMessage="Loading paper trade timeline placeholders."
          emptyMessage="No paper trades yet. Timeline entries will appear after approved paper executions."
          errorMessage="Trade timeline failed to load. No live-routing fallback is allowed in this phase."
        />
      </section>
    </div>
  );
}
