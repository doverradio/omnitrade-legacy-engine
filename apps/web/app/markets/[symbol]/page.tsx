type MarketSymbolPageProps = {
  params: {
    symbol: string;
  };
};

export default function MarketSymbolPage({ params }: MarketSymbolPageProps) {
  return (
    <h1 className="text-2xl font-semibold">
      Market {params.symbol} - coming soon
    </h1>
  );
}
