import EquityCurveChart from "@/components/charts/EquityCurveChart";
import PaperPipelineFlow from "@/components/domain/PaperPipelineFlow";
import SummaryCardRow from "@/components/domain/SummaryCardRow";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="mt-1 text-sm text-foreground/70">Static placeholder content for Phase 0.</p>
      </div>

      <SummaryCardRow />

      <PaperPipelineFlow />

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-lg border border-border bg-muted/60 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Equity Curve</h2>
          <div className="mt-3">
            <EquityCurveChart data={[]} />
          </div>
        </section>

        <section className="rounded-lg border border-border bg-muted/60 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">
            Recent Activity
          </h2>
          <p className="mt-3 rounded-md border border-border bg-background/60 p-4 text-sm text-foreground/75">
            No signals or trades yet — activity will appear here once strategies are active.
          </p>
        </section>
      </div>
    </div>
  );
}
