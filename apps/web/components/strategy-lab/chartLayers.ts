import type { ReplayEvent } from "@/lib/api/strategyLabOffline";

export type ChartLayerId =
  | "bullish_candles"
  | "bearish_candles"
  | "buy_limit"
  | "initial_stop"
  | "trailing_floor"
  | "buy_fill"
  | "sell_fill"
  | "cancelled_buy"
  | "profit_activation";

export type ChartLayerDefinition = {
  id: ChartLayerId;
  name: string;
  symbol: string;
  color: string;
  purpose: string;
  eventKinds?: ReplayEvent["kind"][];
};

export const CHART_LAYERS: ChartLayerDefinition[] = [
  { id: "bullish_candles", name: "Bullish candle", symbol: "▮", color: "#29b17e", purpose: "A completed candle whose close is at or above its open." },
  { id: "bearish_candles", name: "Bearish candle", symbol: "▮", color: "#d95d54", purpose: "A completed candle whose close is below its open." },
  { id: "buy_limit", name: "BUY limit / replacement", symbol: "↺", color: "#e0b34c", purpose: "The pending limit price waiting to enter. Each new point replaces the prior unfilled order." , eventKinds: ["buy_limit"] },
  { id: "initial_stop", name: "Initial stop", symbol: "━", color: "#9b7ede", purpose: "The fixed protective exit floor initialized when a BUY fills, before Profit Mode activates.", eventKinds: ["protective_stop"] },
  { id: "trailing_floor", name: "Trailing floor", symbol: "━", color: "#e58a3a", purpose: "The monotonic exit floor protecting accumulated profit after Profit Mode activates.", eventKinds: ["trailing_floor"] },
  { id: "buy_fill", name: "BUY fill / entry", symbol: "●", color: "#31c48d", purpose: "A BUY limit touched by the market and converted into an open simulated position.", eventKinds: ["entry", "filled_order"] },
  { id: "sell_fill", name: "SELL fill / exit", symbol: "×", color: "#ef6b5f", purpose: "The simulated position exit, labeled with the engine's exact exit reason.", eventKinds: ["exit"] },
  { id: "cancelled_buy", name: "Cancelled BUY", symbol: "⚠", color: "#8b9691", purpose: "An unfilled BUY limit cancelled when the strategy replaced it after the candle closed.", eventKinds: ["cancelled_order"] },
  { id: "profit_activation", name: "Profit Mode activated", symbol: "◆", color: "#37b7c5", purpose: "The threshold event that enables the monotonic trailing floor on subsequent checks.", eventKinds: ["profit_activation"] },
];

export const DEFAULT_VISIBLE_LAYERS = Object.fromEntries(
  CHART_LAYERS.map((layer) => [layer.id, true]),
) as Record<ChartLayerId, boolean>;

export const layerForEvent = (kind: ReplayEvent["kind"]): ChartLayerDefinition | undefined =>
  CHART_LAYERS.find((layer) => layer.eventKinds?.includes(kind));