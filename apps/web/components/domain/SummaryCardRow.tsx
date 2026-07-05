const summaryCards = [
  {
    label: "Paper Balance",
    value: "$25.00",
    note: "example data",
  },
  {
    label: "Today's P&L",
    value: "+$0.31 (+1.2%)",
    note: "example data",
  },
  {
    label: "Open Positions",
    value: "1",
    note: "example data",
  },
  {
    label: "Active Strategies",
    value: "1",
    note: "example data",
  },
];

export default function SummaryCardRow() {
  return (
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Summary cards">
      {summaryCards.map((card) => (
        <article key={card.label} className="rounded-lg border border-border bg-muted/60 p-4">
          <p className="text-xs uppercase tracking-wide text-foreground/70">{card.label}</p>
          <p className="mt-2 text-xl font-semibold">{card.value}</p>
          <p className="mt-2 text-xs text-foreground/60">{card.note}</p>
        </article>
      ))}
    </section>
  );
}
