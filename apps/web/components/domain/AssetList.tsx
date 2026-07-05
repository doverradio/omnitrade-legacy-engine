"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiRequestError, getMarketsAssets, type MarketAsset } from "@/lib/api/markets";

type AssetListProps = {
  selectedAssetId: string | null;
  onSelectAsset: (asset: MarketAsset) => void;
  onErrorChange: (message: string | null) => void;
};

export default function AssetList({ selectedAssetId, onSelectAsset, onErrorChange }: AssetListProps) {
  const [assets, setAssets] = useState<MarketAsset[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;

    async function loadAssets() {
      setLoading(true);
      try {
        const items = await getMarketsAssets({ isActive: true });
        if (!isMounted) {
          return;
        }

        setAssets(items);
        onErrorChange(null);

        if (items.length > 0) {
          onSelectAsset(items[0]);
        }
      } catch (error) {
        if (!isMounted) {
          return;
        }

        const message = error instanceof ApiRequestError ? error.message : "Failed to load assets";
        onErrorChange(message);
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    }

    void loadAssets();

    return () => {
      isMounted = false;
    };
  }, [onErrorChange, onSelectAsset]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return assets;
    }

    return assets.filter((asset) => {
      return (
        asset.symbol.toLowerCase().includes(query) ||
        asset.asset_class.toLowerCase().includes(query) ||
        asset.exchange.toLowerCase().includes(query)
      );
    });
  }, [assets, search]);

  return (
    <section className="flex h-full flex-col rounded-xl border border-border bg-muted/40 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/90">Assets</h2>
        <span className="text-xs text-foreground/70">{assets.length}</span>
      </div>

      <input
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="Search symbol or exchange"
        className="mb-3 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-accent"
      />

      {loading ? (
        <ul className="space-y-2">
          {Array.from({ length: 8 }).map((_, index) => (
            <li key={index} className="h-12 animate-pulse rounded-md bg-foreground/10" />
          ))}
        </ul>
      ) : assets.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-4 text-sm text-foreground/80">
          No assets configured yet - run the seed script or check ingestion status
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-foreground/70">No assets match your search.</p>
      ) : (
        <ul className="space-y-2 overflow-y-auto">
          {filtered.map((asset) => {
            const isSelected = asset.id === selectedAssetId;
            return (
              <li key={asset.id}>
                <button
                  type="button"
                  onClick={() => onSelectAsset(asset)}
                  className={[
                    "flex w-full items-center justify-between rounded-md border px-3 py-2 text-left transition",
                    isSelected
                      ? "border-accent bg-accent/20"
                      : "border-transparent bg-background/20 hover:border-border hover:bg-background/40",
                  ].join(" ")}
                >
                  <div>
                    <p className="text-sm font-medium">{asset.symbol}</p>
                    <p className="text-xs text-foreground/70">{asset.asset_class}</p>
                  </div>
                  <span className="rounded-full border border-border px-2 py-0.5 text-xs text-foreground/80">
                    {asset.exchange}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
