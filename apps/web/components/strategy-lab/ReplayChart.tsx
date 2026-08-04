"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import { CHART_LAYERS, layerForEvent, type ChartLayerId } from "./chartLayers";
import type { PatternAnnotation, ReplayCandle, ReplayEvent, ReplayTrade } from "@/lib/api/strategyLabOffline";

type ReplayChartProps = {
  candles: ReplayCandle[];
  events: ReplayEvent[];
  trades: ReplayTrade[];
  visibleLayers: Record<ChartLayerId, boolean>;
  selectedTrade: ReplayTrade | null;
  visibleCandleCount: number;
  cursorTimestamp: string | null;
  showAllEvents: boolean;
  patternAnnotations: PatternAnnotation[];
  analysisRange: [number, number] | null;
  onLayerToggle: (layer: ChartLayerId) => void;
  onTradeHover: (trade: ReplayTrade | null) => void;
  onTradeSelect: (trade: ReplayTrade | null) => void;
  onCurrentCandleChange: (timestamp: string | null) => void;
  onAnalysisRangeChange: (range: [number, number]) => void;
  onAnnotationSelect: (detailsRef: string) => void;
};

type HoverEvidence = { candle: ReplayCandle; events: ReplayEvent[] };

const toTime = (timestamp: string) => Math.floor(Date.parse(timestamp) / 1000) as UTCTimestamp;
const dollars = (value: string | number) => Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function uniqueLine(source: ReplayEvent[]) {
  const byTime = new Map<number, ReplayEvent>();
  source.forEach((event) => byTime.set(Number(toTime(event.timestamp)), event));
  return Array.from(byTime.values()).map((event) => ({ time: toTime(event.timestamp), value: Number(event.price) }));
}

export default function ReplayChart({ candles, events, trades, visibleLayers, selectedTrade, visibleCandleCount, cursorTimestamp, showAllEvents, patternAnnotations, analysisRange, onLayerToggle, onTradeHover, onTradeSelect, onCurrentCandleChange, onAnalysisRangeChange, onAnnotationSelect }: ReplayChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const orderSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const stopSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const trailingSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const hoverTradeRef = useRef(onTradeHover);
  const selectTradeRef = useRef(onTradeSelect);
  const currentCandleRef = useRef(onCurrentCandleChange);
  const rangeChangeRef = useRef(onAnalysisRangeChange);
  const annotationSelectRef = useRef(onAnnotationSelect);
  const annotationsRef = useRef(patternAnnotations);
  const dragStartRef = useRef<number | null>(null);
  const candlesRef = useRef(candles);
  const eventsRef = useRef(events);
  const tradesRef = useRef(trades);
  const [legendOpen, setLegendOpen] = useState(true);
  const [explainedLayer, setExplainedLayer] = useState<ChartLayerId | null>(null);
  const [hoverEvidence, setHoverEvidence] = useState<HoverEvidence | null>(null);

  const selectedRange = useMemo(() => {
    if (!selectedTrade) return null;
    const tradeIndex = trades.indexOf(selectedTrade);
    const previousExit = tradeIndex > 0 ? trades[tradeIndex - 1].exit_candle_index : -1;
    return { from: previousExit + 1, to: selectedTrade.exit_candle_index };
  }, [selectedTrade, trades]);

  const displayedEvents = useMemo(() => selectedRange
    ? events.filter((event) => event.candle_index >= selectedRange.from && event.candle_index <= selectedRange.to && event.candle_index < visibleCandleCount)
    : events.filter((event) => event.candle_index < visibleCandleCount),
  [events, selectedRange, visibleCandleCount]);

  useEffect(() => {
    hoverTradeRef.current = onTradeHover;
    selectTradeRef.current = onTradeSelect;
    currentCandleRef.current = onCurrentCandleChange;
    rangeChangeRef.current = onAnalysisRangeChange;
    annotationSelectRef.current = onAnnotationSelect;
    annotationsRef.current = patternAnnotations;
    candlesRef.current = candles;
    eventsRef.current = displayedEvents;
    tradesRef.current = trades;
  }, [candles, displayedEvents, onAnalysisRangeChange, onAnnotationSelect, onCurrentCandleChange, onTradeHover, onTradeSelect, patternAnnotations, trades]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#08110f" }, textColor: "#9fb0aa" },
      crosshair: { mode: CrosshairMode.Normal },
      grid: { vertLines: { color: "#15231f" }, horzLines: { color: "#15231f" } },
      rightPriceScale: { borderColor: "#294139", scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: "#294139", timeVisible: true, secondsVisible: false },
      handleScroll: true,
      handleScale: true,
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#29b17e", downColor: "#d95d54", borderVisible: false, wickUpColor: "#29b17e", wickDownColor: "#d95d54",
    });
    const orderSeries = chart.addSeries(LineSeries, { color: "#e0b34c", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "BUY LIMIT" });
    const stopSeries = chart.addSeries(LineSeries, { color: "#9b7ede", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "INITIAL STOP" });
    const trailingSeries = chart.addSeries(LineSeries, { color: "#e58a3a", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "TRAILING FLOOR" });
    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    orderSeriesRef.current = orderSeries;
    stopSeriesRef.current = stopSeries;
    trailingSeriesRef.current = trailingSeries;

    chart.subscribeCrosshairMove((parameter) => {
      if (!parameter.time) {
        setHoverEvidence(null);
        hoverTradeRef.current(null);
        currentCandleRef.current(null);
        return;
      }
      const timestamp = Number(parameter.time);
      const candle = candlesRef.current.find((item) => Number(toTime(item.timestamp)) === timestamp);
      setHoverEvidence(candle ? { candle, events: eventsRef.current.filter((event) => Number(toTime(event.timestamp)) === timestamp) } : null);
      currentCandleRef.current(candle ? new Date(Date.parse(candle.timestamp)).toISOString() : null);
      hoverTradeRef.current(tradesRef.current.find((trade) => timestamp >= Number(toTime(trade.entry_timestamp)) && timestamp <= Number(toTime(trade.exit_timestamp))) ?? null);
    });
    chart.subscribeClick((parameter) => {
      if (!parameter.time) return;
      const timestamp = Number(parameter.time);
      const annotation = annotationsRef.current.find((item) => Number(toTime(item.start_time)) === timestamp);
      if (annotation) {
        annotationSelectRef.current(annotation.details_ref);
        return;
      }
      selectTradeRef.current(tradesRef.current.find((trade) => timestamp >= Number(toTime(trade.entry_timestamp)) && timestamp <= Number(toTime(trade.exit_timestamp))) ?? null);
    });
    const observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    const orderSeries = orderSeriesRef.current;
    const stopSeries = stopSeriesRef.current;
    const trailingSeries = trailingSeriesRef.current;
    if (!chart || !candleSeries || !orderSeries || !stopSeries || !trailingSeries) return;

    candleSeries.applyOptions({
      upColor: visibleLayers.bullish_candles ? "#29b17e" : "rgba(41,177,126,.12)",
      wickUpColor: visibleLayers.bullish_candles ? "#29b17e" : "rgba(41,177,126,.12)",
      downColor: visibleLayers.bearish_candles ? "#d95d54" : "rgba(217,93,84,.12)",
      wickDownColor: visibleLayers.bearish_candles ? "#d95d54" : "rgba(217,93,84,.12)",
    });
    const visibleCandles = candles.slice(0, visibleCandleCount);
    candleSeries.setData(visibleCandles.map((item) => ({
      time: toTime(item.timestamp), open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close),
    })));
    orderSeries.setData(visibleLayers.buy_limit ? uniqueLine(displayedEvents.filter((event) => event.kind === "buy_limit")) : []);
    stopSeries.setData(visibleLayers.initial_stop ? uniqueLine(displayedEvents.filter((event) => event.kind === "protective_stop")) : []);
    trailingSeries.setData(visibleLayers.trailing_floor ? uniqueLine(displayedEvents.filter((event) => event.kind === "trailing_floor")) : []);

    const markerEvents = displayedEvents
      .filter((event) => ["entry", "exit", "profit_activation", "cancelled_order"].includes(event.kind))
      .filter((event) => visibleLayers[layerForEvent(event.kind)?.id ?? "buy_fill"])
      .filter((event) => event.kind !== "cancelled_order" || selectedTrade || showAllEvents || event.candle_index % 24 === 0);
    const strategyMarkers = markerEvents.map((event) => {
      const layer = layerForEvent(event.kind)!;
      return {
        time: toTime(event.timestamp),
        position: event.kind === "exit" ? "aboveBar" as const : "belowBar" as const,
        color: layer.color,
        shape: event.kind === "exit" ? "arrowDown" as const : event.kind === "cancelled_order" ? "circle" as const : "arrowUp" as const,
        text: event.kind === "exit" ? `SELL · ${event.reason?.replaceAll("_", " ") ?? "exit"}` : event.kind === "profit_activation" ? "PROFIT MODE" : event.kind === "cancelled_order" ? "CANCEL" : "BUY FILL",
      };
    });
    const intelligenceMarkers = patternAnnotations
      .filter((annotation) => Number(toTime(annotation.start_time)) <= Number(toTime(candles[Math.max(0, visibleCandleCount - 1)]?.timestamp ?? candles[0].timestamp)))
      .map((annotation) => ({ time: toTime(annotation.start_time), position: "aboveBar" as const, color: "#58b8d8", shape: "square" as const, text: annotation.label }));
    const rangeMarkers = analysisRange ? [
      { time: toTime(candles[analysisRange[0]].timestamp), position: "belowBar" as const, color: "#f2c14e", shape: "circle" as const, text: "ANALYSIS START" },
      { time: toTime(candles[analysisRange[1]].timestamp), position: "belowBar" as const, color: "#f2c14e", shape: "circle" as const, text: "ANALYSIS END" },
    ] : [];
    createSeriesMarkers(candleSeries, [...strategyMarkers, ...intelligenceMarkers, ...rangeMarkers]);

    if (selectedTrade && selectedRange) {
      chart.timeScale().setVisibleRange({ from: toTime(candles[Math.max(0, selectedRange.from)].timestamp), to: toTime(selectedTrade.exit_timestamp) });
    } else {
      chart.timeScale().fitContent();
    }
  }, [analysisRange, candles, displayedEvents, patternAnnotations, selectedRange, selectedTrade, showAllEvents, visibleCandleCount, visibleLayers]);

  const candleIndexAtPointer = (clientX: number) => {
    const chart = chartRef.current;
    const container = containerRef.current;
    if (!chart || !container) return null;
    const time = chart.timeScale().coordinateToTime(clientX - container.getBoundingClientRect().left);
    if (!time) return null;
    const timestamp = Number(time);
    const exact = candles.findIndex((item) => Number(toTime(item.timestamp)) === timestamp);
    if (exact >= 0) return exact;
    return candles.reduce((nearest, item, index) => Math.abs(Number(toTime(item.timestamp)) - timestamp) < Math.abs(Number(toTime(candles[nearest].timestamp)) - timestamp) ? index : nearest, 0);
  };

  const beginRange = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    dragStartRef.current = candleIndexAtPointer(event.clientX);
  };

  const finishRange = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = dragStartRef.current;
    dragStartRef.current = null;
    const end = candleIndexAtPointer(event.clientX);
    if (start === null || end === null || start === end) return;
    rangeChangeRef.current([Math.min(start, end), Math.max(start, end)]);
  };

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!chart || !series) return;
    if (!cursorTimestamp) { chart.clearCrosshairPosition(); return; }
    const candle = candles.find((item) => toTime(item.timestamp) === toTime(cursorTimestamp));
    if (candle) chart.setCrosshairPosition(Number(candle.close), toTime(candle.timestamp), series);
  }, [candles, cursorTimestamp]);

  return (
    <div className="relative h-[440px] min-h-[340px] w-full lg:h-[560px]" aria-label="Deterministic candle replay chart" onPointerDownCapture={beginRange} onPointerUpCapture={finishRange}>
      <div ref={containerRef} className="absolute inset-0" />
      <div className="absolute right-3 top-3 z-20 w-[230px] border border-[#416357] bg-[#0c1714]/95 shadow-xl backdrop-blur-sm">
        <button type="button" className="flex w-full items-center justify-between px-3 py-2 font-mono text-[10px] uppercase text-[#dce7e1]" onClick={() => setLegendOpen((value) => !value)} aria-expanded={legendOpen}>
          <span>Chart legend</span><span aria-hidden="true">{legendOpen ? "−" : "+"}</span>
        </button>
        {legendOpen && <div className="border-t border-[#294139] p-2">
          {CHART_LAYERS.map((layer) => <button key={layer.id} type="button" title={`${layer.name}\n${layer.purpose}`} aria-pressed={visibleLayers[layer.id]} onClick={() => onLayerToggle(layer.id)} onMouseEnter={() => setExplainedLayer(layer.id)} onMouseLeave={() => setExplainedLayer(null)} onFocus={() => setExplainedLayer(layer.id)} onBlur={() => setExplainedLayer(null)} className={`group flex w-full items-center gap-2 px-1.5 py-1 text-left font-mono text-[10px] transition-opacity ${visibleLayers[layer.id] ? "opacity-100" : "opacity-35"}`}>
            <span className="w-4 text-center text-sm" style={{ color: layer.color }} aria-hidden="true">{layer.symbol}</span>
            <span className="flex-1 text-[#c7d4ce]">{layer.name}</span>
            <span className="text-[#6f817a] opacity-0 group-hover:opacity-100">?</span>
          </button>)}
          <p className="mt-2 min-h-12 border-t border-[#294139] px-1 pt-2 font-mono text-[9px] leading-4 text-[#82978e]">{explainedLayer
            ? CHART_LAYERS.find((layer) => layer.id === explainedLayer)?.purpose
            : "Click to show or fade. Cancel markers are sampled every 24 candles in the full run; selecting a trade reveals every cancellation in its lifecycle."}</p>
        </div>}
      </div>
      {hoverEvidence && <ChartTooltip evidence={hoverEvidence} events={events} />}
      {selectedTrade && <div className="absolute bottom-3 left-3 z-20 border border-[#31c48d] bg-[#0c1714]/95 px-3 py-2 font-mono text-[10px] text-[#b9cec4]">FOCUSED TRADE · {selectedTrade.entry_timestamp.slice(0, 16).replace("T", " ")} · click outside a trade to clear</div>}
    </div>
  );
}

function ChartTooltip({ evidence, events }: { evidence: HoverEvidence; events: ReplayEvent[] }) {
  const currentPrice = Number(evidence.candle.close);
  return <div className="pointer-events-none absolute left-3 top-3 z-20 max-w-[255px] border border-[#416357] bg-[#0c1714]/95 p-3 shadow-xl">
    <p className="font-mono text-[9px] uppercase text-[#82978e]">{evidence.candle.timestamp.slice(0, 16).replace("T", " ")} UTC</p>
    <div className="mt-1 grid grid-cols-4 gap-2 font-mono text-[10px] text-[#b9c8c2]"><span>O {dollars(evidence.candle.open)}</span><span>H {dollars(evidence.candle.high)}</span><span>L {dollars(evidence.candle.low)}</span><span>C {dollars(evidence.candle.close)}</span></div>
    {evidence.events.map((event, index) => {
      const layer = layerForEvent(event.kind);
      if (!layer) return null;
      const entry = [...events].reverse().find((candidate) => candidate.kind === "entry" && candidate.candle_index <= event.candle_index);
      const floorEvents = entry ? events.filter((candidate) => ["protective_stop", "trailing_floor"].includes(candidate.kind) && candidate.candle_index >= entry.candle_index && candidate.candle_index <= event.candle_index) : [];
      const moved = floorEvents.reduce((count, candidate, floorIndex) => floorIndex > 0 && candidate.price !== floorEvents[floorIndex - 1].price ? count + 1 : count, 0);
      return <div key={`${event.kind}-${index}`} className="mt-2 border-l-2 pl-2" style={{ borderColor: layer.color }}>
        <p className="font-mono text-[10px] font-semibold" style={{ color: layer.color }}>{layer.name.toUpperCase()}</p>
        <p className="mt-0.5 font-mono text-xs text-white">{dollars(event.price)}</p>
        {event.kind === "buy_limit" && <p className="font-mono text-[9px] text-[#9fb0aa]">Current {dollars(currentPrice)} · distance {Math.abs((currentPrice - Number(event.price)) / currentPrice * 100).toFixed(2)}%</p>}
        {["protective_stop", "trailing_floor"].includes(event.kind) && entry && <p className="font-mono text-[9px] text-[#9fb0aa]">Initialized {event.candle_index - entry.candle_index} candles ago · moved {moved} times</p>}
        {event.reason && <p className="font-mono text-[9px] text-[#9fb0aa]">Reason: {event.reason.replaceAll("_", " ")}</p>}
      </div>;
    })}
  </div>;
}