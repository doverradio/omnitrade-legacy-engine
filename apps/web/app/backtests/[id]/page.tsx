type BacktestDetailPageProps = {
  params: {
    id: string;
  };
};

export default function BacktestDetailPage({ params }: BacktestDetailPageProps) {
  return (
    <h1 className="text-2xl font-semibold">
      Backtest {params.id} - coming soon
    </h1>
  );
}
