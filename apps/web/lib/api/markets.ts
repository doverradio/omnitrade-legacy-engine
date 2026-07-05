export type MarketAsset = {
  id: string;
  symbol: string;
  asset_class: "crypto" | "stock";
  exchange: string;
  is_active: boolean;
  supports_fractional?: boolean;
  min_order_notional?: string | null;
  qty_step_size?: string | null;
};

export type MarketCandle = {
  open_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
};

type MarketsAssetsResponse = {
  items: MarketAsset[];
};

type MarketsCandlesResponse = {
  asset_id: string;
  interval: string;
  items: MarketCandle[];
};

type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, query: URLSearchParams): Promise<T> {
  const url = `${API_BASE_URL}${path}?${query.toString()}`;
  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as ErrorEnvelope;
      if (payload.error?.message) {
        message = payload.error.message;
      }
    } catch {
      // Keep generic message when the response body is not valid JSON.
    }
    throw new ApiRequestError(message, response.status);
  }

  return (await response.json()) as T;
}

export async function getMarketsAssets(params?: {
  assetClass?: "crypto" | "stock";
  isActive?: boolean;
}): Promise<MarketAsset[]> {
  const query = new URLSearchParams();
  if (params?.assetClass) {
    query.set("asset_class", params.assetClass);
  }
  if (typeof params?.isActive === "boolean") {
    query.set("is_active", String(params.isActive));
  }

  const payload = await requestJson<MarketsAssetsResponse>("/markets/assets", query);
  return payload.items;
}

export async function getMarketCandles(params: {
  assetId: string;
  interval: "1m" | "5m" | "15m" | "1h" | "1d";
  startTime?: string;
  endTime?: string;
}): Promise<MarketCandle[]> {
  const query = new URLSearchParams();
  query.set("asset_id", params.assetId);
  query.set("interval", params.interval);
  if (params.startTime) {
    query.set("start_time", params.startTime);
  }
  if (params.endTime) {
    query.set("end_time", params.endTime);
  }

  const payload = await requestJson<MarketsCandlesResponse>("/markets/candles", query);
  return payload.items;
}
