"use client";

import { useEffect, useRef, useState } from "react";
import {
  BaselineSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import type { ReplayResult, ReplayTrade } from "@/lib/api/strategyLabOffline";

type Props = {
  run: ReplayResult;
  visibleCandleCount: number;
  cursorTimestamp: string | null;
  selectedTrade: ReplayTrade | null;
  onCursorChange: (timestamp: string | null) => void;
};

const toTime = (timestamp: string) => Math.floor(Date.parse(timestamp) / 1000) as UTCTimestamp;

export default function CapitalChart({ run, visibleCandleCount, cursorTimestamp, selectedTrade, onCursorChange }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const capitalRef = useRef<ISeriesApi<"Baseline"> | null>(null);
  const buyHoldRef = useRef<ISeriesApi<"Line"> | null>(null);
  const withdrawnRef = useRef<ISeriesApi<"Line"> | null>(null);
  const economicRef = useRef<ISeriesApi<"Line"> | null>(null);
  const cursorCallbackRef = useRef(onCursorChange);
  const [showBuyHold, setShowBuyHold] = useState(true);
  const [showWithdrawn, setShowWithdrawn] = useState(false);
  const [showEconomic, setShowEconomic] = useState(true);

  useEffect(() => { cursorCallbackRef.current = onCursorChange; }, [onCursorChange]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      width: container.clientWidth, height: container.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#08110f" }, textColor: "#9fb0aa" },
      crosshair: { mode: CrosshairMode.Normal },
      grid: { vertLines: { color: "#15231f" }, horzLines: { color: "#15231f" } },
      rightPriceScale: { borderColor: "#294139" },
      timeScale: { borderColor: "#294139", timeVisible: true, secondsVisible: false },
    });
    const startingCapital = Number(run.parameters.initial_capital);
    const capital = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: startingCapital },
      topLineColor: "#32e58f", topFillColor1: "rgba(50,229,143,.22)", topFillColor2: "rgba(50,229,143,.03)",
      bottomLineColor: "#ff6258", bottomFillColor1: "rgba(255,98,88,.03)", bottomFillColor2: "rgba(255,98,88,.22)",
      lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: "TRADING CAPITAL",
    });
    const buyHold = chart.addSeries(LineSeries, { color: "#55a9d6", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "BUY & HOLD" });
    const withdrawn = chart.addSeries(LineSeries, { color: "#d6c55a", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "WITHDRAWN" });
    const economic = chart.addSeries(LineSeries, { color: "#e7edf0", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "TOTAL ECONOMIC VALUE" });
    capital.createPriceLine({ price: startingCapital, color: "#8b9691", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: `START $${startingCapital.toFixed(2)}` });
    chart.subscribeCrosshairMove((parameter) => cursorCallbackRef.current(parameter.time ? new Date(Number(parameter.time) * 1000).toISOString() : null));
    chartRef.current = chart;
    capitalRef.current = capital;
    buyHoldRef.current = buyHold;
    withdrawnRef.current = withdrawn;
    economicRef.current = economic;
    const observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
    observer.observe(container);
    return () => { observer.disconnect(); chart.remove(); chartRef.current = null; };
  }, [run.parameters.initial_capital]);

  useEffect(() => {
    const capital = capitalRef.current;
    if (!capital) return;
    const visible = run.capital_curve.slice(0, visibleCandleCount);
    const map = (key: "trading_capital" | "buy_and_hold" | "withdrawn_profit" | "total_economic_value") => visible.map((point) => ({ time: toTime(point.timestamp), value: Number(point[key]) }));
    capital.setData(map("trading_capital"));
    buyHoldRef.current?.setData(showBuyHold ? map("buy_and_hold") : []);
    withdrawnRef.current?.setData(showWithdrawn ? map("withdrawn_profit") : []);
    economicRef.current?.setData(showEconomic ? map("total_economic_value") : []);
    createSeriesMarkers(capital, selectedTrade ? [
      { time: toTime(selectedTrade.entry_timestamp), position: "belowBar" as const, color: "#31c48d", shape: "arrowUp" as const, text: "ENTRY" },
      { time: toTime(selectedTrade.exit_timestamp), position: "aboveBar" as const, color: "#ef6b5f", shape: "arrowDown" as const, text: `P&L $${Number(selectedTrade.net_pnl).toFixed(2)}` },
    ] : []);
    chartRef.current?.timeScale().fitContent();
  }, [run.capital_curve, selectedTrade, showBuyHold, showEconomic, showWithdrawn, visibleCandleCount]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = capitalRef.current;
    if (!chart || !series) return;
    if (!cursorTimestamp) { chart.clearCrosshairPosition(); return; }
    const point = run.capital_curve.find((item) => toTime(item.timestamp) === toTime(cursorTimestamp));
    if (point) chart.setCrosshairPosition(Number(point.trading_capital), toTime(point.timestamp), series);
  }, [cursorTimestamp, run.capital_curve]);

  return <section className="border-t border-[#294139]" aria-label="Capital and benchmark chart">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#294139] bg-[#0c1714] px-3 py-2">
      <div><p className="font-mono text-[9px] uppercase text-[#6fa78f]">Capital / equity</p><p className="font-mono text-[10px] text-[#82978e]">Independent USD axis · synchronized time</p></div>
      <div className="flex flex-wrap gap-3">
        <CurveToggle label="Buy & Hold" checked={showBuyHold} onChange={setShowBuyHold} color="#55a9d6" />
        <CurveToggle label="Withdrawn" checked={showWithdrawn} onChange={setShowWithdrawn} color="#d6c55a" />
        <CurveToggle label="Economic Value" checked={showEconomic} onChange={setShowEconomic} color="#e7edf0" />
      </div>
    </div>
    <div ref={containerRef} className="h-[210px] w-full" />
  </section>;
}

function CurveToggle({ label, checked, onChange, color }: { label: string; checked: boolean; onChange: (value: boolean) => void; color: string }) {
  return <label className="flex items-center gap-1.5 font-mono text-[9px] text-[#aebdb7]"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} style={{ accentColor: color }} />{label}</label>;
}
