"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";

import { CHART_LAYERS, DEFAULT_VISIBLE_LAYERS, layerForEvent, type ChartLayerId } from "@/components/strategy-lab/chartLayers";
import DatasetUploadDialog from "@/components/strategy-lab/DatasetUploadDialog";
import ResearchCopilotPanel from "@/components/strategy-lab/ResearchCopilotPanel";
import {
  analyzePatternSelection,
  analyzePatternTrade,
  analyzePatternVisibleWindow,
  getOfflineDatasets,
  replayOfflineStrategy,
  type PatternAnalysis,
  type PatternAnalysisRequest,
  type PatternFinding,
  type ReplayRequest,
  type ReplayResult,
  type ReplayTrade,
  type ResearchPeriod,
  type StrategyLabDataset,
  type StrategyLabParameters,
  type StrategyVersion,
} from "@/lib/api/strategyLabOffline";

const ReplayChart = dynamic(() => import("@/components/strategy-lab/ReplayChart"), { ssr: false });
const CapitalChart = dynamic(() => import("@/components/strategy-lab/CapitalChart"), { ssr: false });

type ViewMode = "instant" | "playback";
type PlaybackSpeed = 1 | 5 | 20 | 100;
type SavedExperiment = { run: ReplayResult; replayDate: string; parameterSet: string };

const DEFAULT_PARAMETERS: StrategyLabParameters = {
  entry_offset_pct: "0.01",
  initial_stop_pct: "0.01",
  profit_activation_pct: "0.03",
  trailing_distance_pct: "0.01",
  required_declining_candles: 2,
  fee_pct: "0.002",
  slippage_pct: "0.0005",
  initial_capital: "100",
  trade_deployment_pct: "100",
  profit_compound_pct: "100",
  profit_withdrawal_pct: "0",
  profit_tax_reserve_pct: "0",
};

const PARAMETER_FIELDS: Array<{ key: keyof StrategyLabParameters; label: string; step: string }> = [
  { key: "entry_offset_pct", label: "Entry offset", step: "0.001" },
  { key: "initial_stop_pct", label: "Initial stop", step: "0.001" },
  { key: "profit_activation_pct", label: "Profit activation", step: "0.001" },
  { key: "trailing_distance_pct", label: "Trailing distance", step: "0.001" },
  { key: "required_declining_candles", label: "Declining candles", step: "1" },
  { key: "fee_pct", label: "Fee rate", step: "0.0001" },
  { key: "slippage_pct", label: "Slippage rate", step: "0.0001" },
  { key: "initial_capital", label: "Starting capital", step: "10" },
  { key: "trade_deployment_pct", label: "Capital deployed %", step: "1" },
  { key: "profit_compound_pct", label: "Profit compounded %", step: "1" },
  { key: "profit_withdrawal_pct", label: "Profit withdrawn %", step: "1" },
  { key: "profit_tax_reserve_pct", label: "Tax reserve %", step: "1" },
];

const number = (value: string | number | null | undefined) => Number(value ?? 0);
const money = (value: string | number | null | undefined) => `$${number(value).toFixed(2)}`;
const percent = (value: string | number | null | undefined) => `${number(value).toFixed(2)}%`;

function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

function reportText(run: ReplayResult) {
  return [
    "OFFLINE STRATEGY LABORATORY REPORT",
    `Dataset: ${run.dataset.id} (${run.dataset.candle_count} candles)`,
    `Strategy: #${run.strategy_version}`,
    `Period: ${run.dataset.first_timestamp} to ${run.dataset.last_timestamp}`,
    `Verdict: ${run.metrics.verdict}`,
    `Net return: ${percent(run.metrics.net_return_pct)}`,
    `Buy & hold: ${percent(run.metrics.buy_and_hold_return_pct)}`,
    `Trades: ${run.metrics.total_trades}`,
    `Max drawdown: ${percent(run.metrics.max_drawdown_pct)}`,
    "",
    "This report is deterministic offline research evidence, not a production trading instruction.",
  ].join("\n");
}

export default function StrategyLabPage() {
  const [datasets, setDatasets] = useState<StrategyLabDataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [strategyVersion, setStrategyVersion] = useState<StrategyVersion>("002");
  const [researchPeriod, setResearchPeriod] = useState<ResearchPeriod>("training");
  const [parameters, setParameters] = useState(DEFAULT_PARAMETERS);
  const [run, setRun] = useState<ReplayResult | null>(null);
  const [savedRuns, setSavedRuns] = useState<SavedExperiment[]>([]);
  const [hoveredTrade, setHoveredTrade] = useState<ReplayTrade | null>(null);
  const [selectedTrade, setSelectedTrade] = useState<ReplayTrade | null>(null);
  const [currentCandle, setCurrentCandle] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "replaying" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [visibleLayers, setVisibleLayers] = useState(DEFAULT_VISIBLE_LAYERS);
  const [showComparison, setShowComparison] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("instant");
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackIndex, setPlaybackIndex] = useState(0);
  const [playbackSpeed, setPlaybackSpeed] = useState<PlaybackSpeed>(5);
  const [inspectorMode, setInspectorMode] = useState<"summary" | "trace">("summary");
  const [analysisRange, setAnalysisRange] = useState<[number, number] | null>(null);
  const [patternAnalysis, setPatternAnalysis] = useState<PatternAnalysis | null>(null);
  const [selectedFinding, setSelectedFinding] = useState<PatternFinding | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState<"idle" | "analyzing" | "error">("idle");
  const [analysisError, setAnalysisError] = useState("");
  const viewModeRef = useRef(viewMode);

  useEffect(() => { viewModeRef.current = viewMode; }, [viewMode]);

  useEffect(() => {
    getOfflineDatasets()
      .then((items) => {
        setDatasets(items);
        setDatasetId(items[0]?.id ?? "");
        if (!items.length) setStatus("error");
      })
      .catch((reason: unknown) => {
        setStatus("error");
        setError(reason instanceof Error ? reason.message : "Offline dataset catalog unavailable");
      });
  }, []);

  const selectedDataset = datasets.find((item) => item.id === datasetId);
  const allocationTotal = number(parameters.profit_compound_pct) + number(parameters.profit_withdrawal_pct) + number(parameters.profit_tax_reserve_pct);
  const replayPayload = useMemo<ReplayRequest | null>(() => {
    if (!datasetId || !selectedDataset || allocationTotal !== 100) return null;
    const first = Date.parse(selectedDataset.first_timestamp);
    const last = Date.parse(selectedDataset.last_timestamp);
    const oneThird = (last - first) / 3;
    const range = researchPeriod === "entire_dataset" ? {}
      : researchPeriod === "training"
      ? { start_time: selectedDataset.first_timestamp, end_time: new Date(first + oneThird).toISOString() }
      : researchPeriod === "validation"
        ? { start_time: new Date(first + oneThird).toISOString(), end_time: new Date(first + oneThird * 2).toISOString() }
        : { start_time: new Date(first + oneThird * 2).toISOString(), end_time: selectedDataset.last_timestamp };
    return { dataset_id: datasetId, strategy_version: strategyVersion, research_period: researchPeriod, parameters, ...range };
  }, [allocationTotal, datasetId, parameters, researchPeriod, selectedDataset, strategyVersion]);

  useEffect(() => {
    if (!replayPayload) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setStatus("replaying");
      setError("");
      replayOfflineStrategy(replayPayload, controller.signal)
        .then((result) => {
          setRun(result);
          setSelectedTrade(null);
          setAnalysisRange(null);
          setPatternAnalysis(null);
          setSelectedFinding(null);
          setCurrentCandle(null);
          setPlaybackIndex(viewModeRef.current === "instant" ? result.candles.length : 0);
          setIsPlaying(false);
          setStatus("ready");
        })
        .catch((reason: unknown) => {
          if (reason instanceof DOMException && reason.name === "AbortError") return;
          setStatus("error");
          setError(reason instanceof Error ? reason.message : "Replay failed");
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [replayPayload]);

  useEffect(() => {
    if (!isPlaying || !run || viewMode !== "playback") return;
    const delay = { 1: 600, 5: 120, 20: 30, 100: 8 }[playbackSpeed];
    const timer = window.setInterval(() => {
      setPlaybackIndex((current) => {
        const next = Math.min(run.candles.length, current + 1);
        if (next >= run.candles.length) setIsPlaying(false);
        setCurrentCandle(run.candles[Math.max(0, next - 1)]?.timestamp ?? null);
        return next;
      });
    }, delay);
    return () => window.clearInterval(timer);
  }, [isPlaying, playbackSpeed, run, viewMode]);

  const updateParameter = (key: keyof StrategyLabParameters, value: string) => {
    setIsPlaying(false);
    setPlaybackIndex(0);
    setParameters((current) => ({ ...current, [key]: key === "required_declining_candles" ? Math.max(2, Math.round(Number(value))) : value }));
  };

  const saveRun = () => {
    if (!run) return;
    const saved = { run, replayDate: new Date().toISOString(), parameterSet: `Entry ${run.parameters.entry_offset_pct} · Stop ${run.parameters.initial_stop_pct} · Trail ${run.parameters.trailing_distance_pct}` };
    setSavedRuns((current) => [saved, ...current.filter((item) => JSON.stringify(item.run.parameters) !== JSON.stringify(run.parameters) || item.run.dataset.id !== run.dataset.id)].slice(0, 8));
  };

  const reopenRun = (saved: SavedExperiment) => {
    setIsPlaying(false);
    setDatasetId(saved.run.dataset.id);
    setStrategyVersion(saved.run.strategy_version);
    setResearchPeriod(saved.run.dataset.research_period);
    setParameters(saved.run.parameters);
    setRun(saved.run);
    setPlaybackIndex(saved.run.candles.length);
    setViewMode("instant");
    setShowComparison(false);
  };

  const changeViewMode = (mode: ViewMode) => {
    setIsPlaying(false);
    setViewMode(mode);
    setPlaybackIndex(mode === "instant" ? run?.candles.length ?? 0 : 0);
  };

  const startOrStopSimulation = () => {
    if (!run) return;
    if (isPlaying) { setIsPlaying(false); return; }
    const start = playbackIndex >= run.candles.length ? 0 : playbackIndex;
    setPlaybackIndex(Math.max(1, start));
    setIsPlaying(true);
  };

  const stepPlayback = (delta: number) => {
    if (!run) return;
    setIsPlaying(false);
    setPlaybackIndex((current) => Math.max(1, Math.min(run.candles.length, current + delta)));
  };

  const toggleLayer = (layer: ChartLayerId) => {
    setVisibleLayers((current) => ({ ...current, [layer]: !current[layer] }));
  };

  const analysisPartition: PatternAnalysisRequest["partition"] = researchPeriod === "out_of_sample" ? "final_test" : researchPeriod;
  const patternPayload = (range: [number, number]): PatternAnalysisRequest => ({
    dataset_id: datasetId,
    strategy_version: strategyVersion,
    selected_start_index: range[0],
    selected_end_index: range[1],
    partition: analysisPartition,
    parameters,
  });
  const runPatternAnalysis = async (request: () => Promise<PatternAnalysis>) => {
    setIsPlaying(false);
    setAnalysisStatus("analyzing");
    setAnalysisError("");
    try {
      const result = await request();
      setPatternAnalysis(result);
      setAnalysisRange(result.selected_range);
      setSelectedFinding(result.findings[0] ?? null);
      setAnalysisStatus("idle");
    } catch (reason: unknown) {
      setAnalysisStatus("error");
      setAnalysisError(reason instanceof Error ? reason.message : "Pattern analysis failed");
    }
  };
  const selectAnalysisRange = (range: [number, number]) => {
    setIsPlaying(false);
    setAnalysisRange(range);
    setPatternAnalysis(null);
    setSelectedFinding(null);
  };
  const analyzeSelection = () => {
    if (analysisRange) void runPatternAnalysis(() => analyzePatternSelection(patternPayload(analysisRange)));
  };
  const analyzeVisibleWindow = () => {
    if (run && visibleCandleCount) void runPatternAnalysis(() => analyzePatternVisibleWindow(patternPayload([0, visibleCandleCount - 1])));
  };
  const analyzeTrade = () => {
    if (!run || !selectedTrade) return;
    const tradeIndex = run.trades.indexOf(selectedTrade);
    void runPatternAnalysis(() => analyzePatternTrade({
      dataset_id: datasetId, strategy_version: strategyVersion, trade_index: tradeIndex,
      partition: analysisPartition, parameters,
    }));
  };
  const openAnnotation = (detailsRef: string) => {
    const finding = patternAnalysis?.findings.find((item) => item.finding_id === detailsRef) ?? null;
    setSelectedFinding(finding);
  };

  const visibleLayerCount = CHART_LAYERS.filter((layer) => visibleLayers[layer.id]).length;
  const visibleCandleCount = run ? (viewMode === "instant" ? run.candles.length : Math.max(1, playbackIndex)) : 0;
  const visibleTrades = run?.trades.filter((trade) => trade.exit_candle_index < visibleCandleCount) ?? [];
  const selectedTradeIndex = run && selectedTrade ? run.trades.indexOf(selectedTrade) : -1;
  const researchSelectionPayload = analysisRange ? patternPayload(analysisRange) : null;
  const researchVisiblePayload = run && visibleCandleCount ? patternPayload([0, visibleCandleCount - 1]) : null;
  const researchTradePayload = selectedTradeIndex >= 0 ? {
    dataset_id: datasetId, strategy_version: strategyVersion, trade_index: selectedTradeIndex,
    partition: analysisPartition, parameters,
  } : null;
  const currentPlaybackEvents = run?.events.filter((event) => event.candle_index === visibleCandleCount - 1) ?? [];
  const fullTradeTrace = useMemo(() => {
    if (!run || !selectedTrade) return [];
    const tradeIndex = run.trades.indexOf(selectedTrade);
    const previousExit = tradeIndex > 0 ? run.trades[tradeIndex - 1].exit_candle_index : -1;
    return run.events
      .filter((event) => event.candle_index > previousExit && event.candle_index <= selectedTrade.exit_candle_index)
      .map((event, index) => ({ event, label: traceLabel(event.kind, index === 0) }));
  }, [run, selectedTrade]);
  const tradeTimeline = useMemo(() => {
    if (!run || !selectedTrade) return [];
    const tradeIndex = run.trades.indexOf(selectedTrade);
    const previousExit = tradeIndex > 0 ? run.trades[tradeIndex - 1].exit_candle_index : -1;
    let limitCount = 0;
    let stopShown = false;
    let priorTrailingPrice: string | null = null;
    return run.events.flatMap((event) => {
      if (event.candle_index <= previousExit || event.candle_index > selectedTrade.exit_candle_index) return [];
      if (event.kind === "buy_limit") limitCount += 1;
      if (event.kind === "protective_stop") {
        if (stopShown) return [];
        stopShown = true;
      }
      if (event.kind === "trailing_floor") {
        if (event.price === priorTrailingPrice) return [];
        priorTrailingPrice = event.price;
      }
      if (event.kind === "entry") return [];
      return [{
        event,
        label: event.kind === "buy_limit" ? (limitCount === 1 ? "BUY LIMIT" : "BUY LIMIT REPLACED")
          : event.kind === "cancelled_order" ? "BUY CANCELLED"
            : event.kind === "filled_order" ? "BUY FILLED"
              : event.kind === "protective_stop" ? "STOP INITIALIZED"
                : event.kind === "profit_activation" ? "PROFIT MODE"
                  : event.kind === "trailing_floor" ? "TRAILING UPDATED"
                    : event.kind === "exit" ? "SELL FILLED"
                      : "TRACE EVENT",
      }];
    });
  }, [run, selectedTrade]);

  const exportTrades = () => {
    if (!run) return;
    const fields = ["entry_timestamp", "exit_timestamp", "effective_entry_price", "effective_exit_price", "net_pnl", "net_return_pct", "mfe_pct", "mae_pct", "holding_candles", "exit_reason"];
    download("strategy-lab-trades.csv", [fields.join(","), ...run.trades.map((trade) => fields.map((field) => String(trade[field] ?? "")).join(","))].join("\n"), "text/csv");
  };

  return (
    <>
      <div className="strategy-lab -m-4 min-h-screen bg-[#08110f] text-[#e6eee9] sm:-m-6">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#294139] bg-[#0c1714] px-4 py-3">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-[#6fa78f]">Offline deterministic research</p>
            <h1 className="font-serif text-2xl">Visual Strategy Laboratory</h1>
          </div>
          <div className="flex items-center gap-3 font-mono text-xs">
            <button className="lab-button" type="button" onClick={() => setShowHelp(true)}>What am I looking at?</button>
            <span className={`h-2 w-2 rounded-full ${status === "error" ? "bg-[#d95d54]" : status === "ready" ? "bg-[#31c48d]" : "animate-pulse bg-[#e0b34c]"}`} />
            <span>{status === "replaying" ? "REPLAYING" : status.toUpperCase()}</span>
            <span className="border-l border-[#294139] pl-3">DB FREE</span>
          </div>
        </header>

        <div className="grid min-h-[calc(100vh-73px)] grid-cols-1 xl:grid-cols-[320px_minmax(600px,1fr)_300px]">
          <aside className="border-b border-[#294139] bg-[#0c1714] p-4 xl:border-b-0 xl:border-r">
            <SectionTitle index="01" title="Experiment controls" />
            <Field label="Dataset">
              <select value={datasetId} onChange={(event) => { setIsPlaying(false); setDatasetId(event.target.value); }} className="lab-input">
                {datasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name}</option>)}
              </select>
            </Field>
            <button className="lab-button mb-3 w-full" type="button" onClick={() => setShowUpload(true)}>Upload Candle CSV</button>
            {selectedDataset && <div className="mb-4 grid grid-cols-2 gap-x-3 gap-y-1 border-l-2 border-[#416357] pl-3 font-mono text-[10px] leading-5 text-[#82978e]"><span>{selectedDataset.candle_count.toLocaleString()} candles</span><span>{selectedDataset.asset} · {selectedDataset.interval}</span><span>{selectedDataset.first_timestamp.slice(0, 10)}</span><span>→ {selectedDataset.last_timestamp.slice(0, 10)}</span><span>Missing {selectedDataset.missing_candles}</span><span>Duplicates {selectedDataset.duplicate_timestamps}</span></div>}
            <Field label="Strategy version">
              <div className="grid grid-cols-2 gap-1" role="group" aria-label="Strategy version">
                {(["001", "002"] as StrategyVersion[]).map((version) => <button key={version} onClick={() => { setIsPlaying(false); setStrategyVersion(version); }} className={`lab-segment ${strategyVersion === version ? "lab-segment-active" : ""}`}>#{version}</button>)}
              </div>
            </Field>
            <Field label="Historical Test Period">
              <select value={researchPeriod} onChange={(event) => { setIsPlaying(false); setResearchPeriod(event.target.value as ResearchPeriod); }} className="lab-input" aria-describedby="period-help">
                <option value="training">Training · develop strategies</option>
                <option value="validation">Validation · unseen history</option>
                <option value="out_of_sample">Final Test · out-of-sample</option>
                <option value="entire_dataset">Entire Dataset · exploratory only</option>
              </select>
              <span id="period-help" className="mt-1 block font-mono text-[9px] leading-4 text-[#82978e]" title="Separating development, validation, and final test periods reduces overfitting by preventing repeated tuning against the same historical evidence.">Training is for development; Validation checks unseen data; Final Test is reserved verification. Separation reduces overfitting.</span>
            </Field>
            <Field label="Visualization mode">
              <div className="grid grid-cols-2 gap-1" role="group" aria-label="Visualization mode"><button className={`lab-segment ${viewMode === "instant" ? "lab-segment-active" : ""}`} onClick={() => changeViewMode("instant")}>Instant Results</button><button className={`lab-segment ${viewMode === "playback" ? "lab-segment-active" : ""}`} onClick={() => changeViewMode("playback")}>Playback Mode</button></div>
            </Field>
            {viewMode === "playback" && <div className="mb-4 border border-[#294139] bg-[#08110f] p-3">
              <button className={`lab-button w-full ${isPlaying ? "border-[#d95d54] text-[#ef8b82]" : "border-[#31c48d] text-[#8ee5bd]"}`} type="button" onClick={startOrStopSimulation}>{isPlaying ? "Stop Simulation" : "Run Simulation"}</button>
              <div className="mt-2 grid grid-cols-5 gap-1"><TransportButton label="Beginning" symbol="|◀" onClick={() => { setIsPlaying(false); setPlaybackIndex(1); }} /><TransportButton label="Previous Candle" symbol="◀" onClick={() => stepPlayback(-1)} /><TransportButton label={isPlaying ? "Pause" : "Play"} symbol={isPlaying ? "Ⅱ" : "▶"} onClick={() => setIsPlaying((value) => !value)} /><TransportButton label="Next Candle" symbol="▶" onClick={() => stepPlayback(1)} /><TransportButton label="End" symbol="▶|" onClick={() => { setIsPlaying(false); setPlaybackIndex(run?.candles.length ?? 1); }} /></div>
              <div className="mt-2 grid grid-cols-4 gap-1" role="group" aria-label="Replay speed">{([1, 5, 20, 100] as PlaybackSpeed[]).map((speed) => <button key={speed} className={`lab-segment min-h-7 text-[10px] ${playbackSpeed === speed ? "lab-segment-active" : ""}`} onClick={() => setPlaybackSpeed(speed)}>{speed}×</button>)}</div>
              <div className="mt-2 flex justify-between font-mono text-[9px] text-[#82978e]"><span>Candle {visibleCandleCount.toLocaleString()}</span><span>{run?.candles.length.toLocaleString() ?? 0}</span></div>
              <div className="mt-2 min-h-10 border-t border-[#294139] pt-2 font-mono text-[9px] leading-4 text-[#9fb0aa]" aria-live="polite"><span className="text-[#6fa78f]">EVENTS · </span>{currentPlaybackEvents.length ? currentPlaybackEvents.map((event) => `${traceLabel(event.kind)} ${money(event.price)}`).join(" · ") : "Candle closed · no strategy event"}</div>
            </div>}
            <div className="my-5 border-t border-[#294139]" />
            <SectionTitle index="02" title="Parameters" />
            <div className="grid grid-cols-2 gap-x-3 gap-y-2 xl:grid-cols-1">
              {PARAMETER_FIELDS.map((field) => (
                <label key={field.key} className="grid grid-cols-[1fr_92px] items-center gap-2 text-xs text-[#aebdb7]">
                  <span>{field.label}</span>
                  <input className="lab-input text-right font-mono" type="number" min="0" step={field.step} value={parameters[field.key]} onChange={(event) => updateParameter(field.key, event.target.value)} />
                </label>
              ))}
            </div>
            {allocationTotal !== 100 && <p role="alert" className="mt-3 border-l-2 border-[#d95d54] pl-2 text-xs text-[#ef8b82]">Profit allocation is {allocationTotal}%. It must equal 100%.</p>}
            <button className="lab-button mt-4 w-full" onClick={() => { setIsPlaying(false); setParameters(DEFAULT_PARAMETERS); }}>Reset parameters</button>
          </aside>

          <main className="min-w-0 border-b border-[#294139] bg-[#08110f] xl:border-b-0 xl:border-r">
            <div className="grid grid-cols-2 gap-px border-b border-[#294139] bg-[#294139] sm:grid-cols-3 lg:grid-cols-6">
              <ChartHeaderDatum label="Dataset" value={selectedDataset ? `${selectedDataset.asset} · ${selectedDataset.interval}` : "—"} />
              <ChartHeaderDatum label="Strategy version" value={`#${strategyVersion}`} />
              <ChartHeaderDatum label="Replay speed" value={viewMode === "instant" ? "Instant · 350ms" : `${playbackSpeed}× · ${visibleCandleCount}/${run?.candles.length ?? 0}`} />
              <ChartHeaderDatum label="Visible layers" value={`${visibleLayerCount} / ${CHART_LAYERS.length}`} />
              <ChartHeaderDatum label="Current candle" value={currentCandle ? currentCandle.slice(0, 16).replace("T", " ") : "Move crosshair"} />
              <ChartHeaderDatum label="Replay status" value={isPlaying ? "PLAYING" : viewMode === "playback" && playbackIndex < (run?.candles.length ?? 0) ? "PAUSED" : status.toUpperCase()} accent={status === "ready" && !isPlaying} />
            </div>
            {run ? <><ReplayChart candles={run.candles} events={run.events} trades={visibleTrades} visibleLayers={visibleLayers} selectedTrade={selectedTrade} visibleCandleCount={visibleCandleCount} cursorTimestamp={currentCandle} showAllEvents={viewMode === "playback"} patternAnnotations={patternAnalysis?.annotations ?? []} analysisRange={analysisRange} onLayerToggle={toggleLayer} onTradeHover={setHoveredTrade} onTradeSelect={setSelectedTrade} onCurrentCandleChange={setCurrentCandle} onAnalysisRangeChange={selectAnalysisRange} onAnnotationSelect={openAnnotation} /><CapitalChart run={run} visibleCandleCount={visibleCandleCount} cursorTimestamp={currentCandle} selectedTrade={selectedTrade} onCursorChange={setCurrentCandle} /></> : (
              <div className="flex h-[420px] items-center justify-center font-mono text-sm text-[#82978e]">{error || "Loading offline candles…"}</div>
            )}
            <div className="border-t border-[#294139] p-4">
              <div className="mb-3 flex items-center justify-between">
                <SectionTitle index="03" title="Trade ledger" />
                <span className="font-mono text-xs text-[#82978e]">{visibleTrades.length} completed</span>
              </div>
              {hoveredTrade && <div className="mb-3 grid grid-cols-3 gap-3 border-l-2 border-[#e0b34c] bg-[#0c1714] p-3 font-mono text-xs sm:grid-cols-6">
                <Datum label="Entry" value={money(hoveredTrade.effective_entry_price)} /><Datum label="Exit" value={money(hoveredTrade.effective_exit_price)} /><Datum label="Net P&L" value={money(hoveredTrade.net_pnl)} /><Datum label="MFE" value={percent(hoveredTrade.mfe_pct)} /><Datum label="MAE" value={percent(hoveredTrade.mae_pct)} /><Datum label="Hold" value={`${hoveredTrade.holding_candles} bars`} />
              </div>}
              {selectedTrade && <div className="mb-3 border border-[#416357] bg-[#0c1714]">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#294139] px-3 py-2"><div><p className="font-mono text-[9px] uppercase text-[#6fa78f]">Trade Inspector</p><p className="mt-1 font-mono text-xs">{selectedTrade.entry_timestamp.slice(0, 16).replace("T", " ")} → {selectedTrade.exit_timestamp.slice(0, 16).replace("T", " ")}</p></div><div className="flex gap-1"><button className={`lab-segment min-h-7 px-2 text-[9px] ${inspectorMode === "summary" ? "lab-segment-active" : ""}`} onClick={() => setInspectorMode("summary")}>Trade Summary</button><button className={`lab-segment min-h-7 px-2 text-[9px] ${inspectorMode === "trace" ? "lab-segment-active" : ""}`} onClick={() => setInspectorMode("trace")}>Complete Engine Trace</button><button className="lab-button" type="button" onClick={() => setSelectedTrade(null)}>Show all</button></div></div>
                {inspectorMode === "summary" && <TradeSummary trade={selectedTrade} timeline={tradeTimeline} />}
                {inspectorMode === "trace" && <ol className="flex max-h-44 gap-0 overflow-auto p-3">
                  {fullTradeTrace.map(({ event, label }, index) => {
                    const layer = layerForEvent(event.kind);
                    return <li key={`${event.kind}-${event.candle_index}-${index}`} className="min-w-[150px] border-l-2 pl-3 pr-4 font-mono" style={{ borderColor: layer?.color ?? "#82978e" }}><p className="text-[9px] font-semibold" style={{ color: layer?.color ?? "#aebdb7" }}>{label}</p><p className="mt-1 text-xs text-[#e6eee9]">{money(event.price)}</p><p className="mt-1 text-[9px] text-[#82978e]">{event.timestamp.slice(5, 16).replace("T", " ")}</p>{event.reason && <p className="mt-1 text-[9px] text-[#aebdb7]">{event.reason.replaceAll("_", " ")}</p>}</li>;
                  })}
                </ol>}
              </div>}
              <div className="max-h-48 overflow-auto">
                <table className="w-full min-w-[680px] border-collapse font-mono text-xs">
                  <thead className="sticky top-0 bg-[#08110f] text-left text-[#82978e]"><tr><th>Entry</th><th>Exit</th><th>P&L</th><th>Return</th><th>MFE</th><th>MAE</th><th>Reason</th></tr></thead>
                  <tbody>{visibleTrades.map((trade, index) => <tr key={`${trade.entry_timestamp}-${index}`} role="button" tabIndex={0} aria-pressed={selectedTrade === trade} onClick={() => { setInspectorMode("summary"); setSelectedTrade(selectedTrade === trade ? null : trade); }} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setSelectedTrade(selectedTrade === trade ? null : trade); }} className={`cursor-pointer border-t border-[#1c2e28] hover:bg-[#12231e] ${selectedTrade === trade ? "bg-[#183228] outline outline-1 outline-[#31c48d]" : ""}`}><td>{trade.entry_timestamp.slice(0, 16).replace("T", " ")}</td><td>{trade.exit_timestamp.slice(0, 16).replace("T", " ")}</td><td className={number(trade.net_pnl) >= 0 ? "text-[#31c48d]" : "text-[#ef6b5f]"}>{money(trade.net_pnl)}</td><td>{percent(number(trade.net_return_pct) * 100)}</td><td>{percent(trade.mfe_pct)}</td><td>{percent(trade.mae_pct)}</td><td>{trade.exit_reason}</td></tr>)}</tbody>
                </table>
              </div>
            </div>
          </main>

          <aside className="bg-[#0c1714] p-4">
            <SectionTitle index="04" title="Pattern Intelligence" />
            <div className="mb-4 border border-[#294139] bg-[#08110f] p-3">
              <p className="font-mono text-[9px] leading-4 text-[#82978e]">Drag across the candle chart to select a range. Selection pauses playback.</p>
              <p className="mt-2 font-mono text-[10px] text-[#dce7e1]">{analysisRange ? `Candles ${analysisRange[0]}–${analysisRange[1]}` : "No candle range selected"}</p>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button className="lab-button" disabled={!analysisRange || analysisStatus === "analyzing"} onClick={analyzeSelection}>Analyze Selection</button>
                <button className="lab-button" disabled={!run || analysisStatus === "analyzing"} onClick={analyzeVisibleWindow}>Visible Window</button>
                <button className="lab-button col-span-2" disabled={!selectedTrade || analysisStatus === "analyzing"} onClick={analyzeTrade}>Analyze Selected Trade</button>
              </div>
              {analysisStatus === "analyzing" && <p className="mt-2 font-mono text-[10px] text-[#e0b34c]">ANALYZING LOCAL CANDLES…</p>}
              {analysisError && <p role="alert" className="mt-2 font-mono text-[10px] text-[#ef6b5f]">{analysisError}</p>}
            </div>
            {patternAnalysis && <PatternIntelligencePanel analysis={patternAnalysis} selected={selectedFinding} onSelect={setSelectedFinding} />}
            <ResearchCopilotPanel
              selectionPayload={researchSelectionPayload}
              visiblePayload={researchVisiblePayload}
              tradePayload={researchTradePayload}
              tradeIsLoss={number(selectedTrade?.net_return_pct) < 0}
              tradeIsWin={number(selectedTrade?.net_return_pct) > 0}
            />
            <div className="my-5 border-t border-[#294139]" />
            <SectionTitle index="05" title="Evidence" />
            {run ? <>
              <div className={`mb-4 border-l-4 p-3 ${run.metrics.verdict === "PROFITABLE" ? "border-[#31c48d] bg-[#10251e]" : run.metrics.verdict === "UNPROFITABLE" ? "border-[#d95d54] bg-[#261514]" : "border-[#e0b34c] bg-[#272214]"}`}>
                <p className="font-mono text-[10px] uppercase text-[#82978e]">Scientific verdict</p>
                <p className="mt-1 font-serif text-xl">{run.metrics.verdict}</p>
              </div>
              <div className="grid grid-cols-2 gap-px bg-[#294139]">
                <Metric label="Net return" value={percent(run.metrics.net_return_pct)} /><Metric label="Buy & hold" value={percent(run.metrics.buy_and_hold_return_pct)} /><Metric label="Economic value" value={money(run.metrics.total_economic_value)} /><Metric label="Outperformance" value={money(run.metrics.outperformance)} /><Metric label="Win rate" value={percent(run.metrics.win_rate_pct)} /><Metric label="Max drawdown" value={percent(run.metrics.max_drawdown_pct)} /><Metric label="Fees" value={money(run.metrics.fees_paid)} /><Metric label="Slippage" value={money(run.metrics.estimated_slippage)} />
              </div>
              <div className="mt-4 space-y-2 border-y border-[#294139] py-3 font-mono text-xs">
                <Row label="Trading capital" value={money(run.metrics.ending_trading_capital)} /><Row label="Withdrawn" value={money(run.metrics.withdrawn_profit)} /><Row label="Tax reserve" value={money(run.metrics.tax_reserve)} /><Row label="Wins / losses" value={`${run.metrics.winning_trades} / ${run.metrics.losing_trades}`} />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-2">
                <button className="lab-button" onClick={saveRun}>Save run</button>
                <button className="lab-button" disabled={savedRuns.length < 2} onClick={() => setShowComparison((value) => !value)}>Compare ({savedRuns.length})</button>
                <button className="lab-button" onClick={exportTrades}>Trades CSV</button>
                <button className="lab-button" onClick={() => download("strategy-lab-run.json", JSON.stringify(run, null, 2), "application/json")}>Run JSON</button>
                <button className="lab-button col-span-2" onClick={() => download("strategy-lab-report.txt", reportText(run), "text/plain")}>Research report</button>
              </div>
              {savedRuns.length > 0 && <div className="mt-5 border-t border-[#294139] pt-4"><SectionTitle index="05" title="Research journal" /><div className="space-y-2">{savedRuns.map((saved) => <button key={saved.replayDate} className="w-full border border-[#294139] bg-[#08110f] p-2 text-left hover:border-[#6fa78f]" onClick={() => reopenRun(saved)}><span className="block font-mono text-[9px] text-[#6fa78f]">{new Date(saved.replayDate).toLocaleString()}</span><span className="mt-1 block font-mono text-[10px] text-[#dce7e1]">{saved.run.dataset.asset} · #{saved.run.strategy_version} · {saved.run.metrics.verdict}</span><span className="mt-1 block truncate font-mono text-[9px] text-[#82978e]">{saved.parameterSet}</span></button>)}</div></div>}
            </> : <p className="text-sm text-[#82978e]">Evidence appears after the first deterministic replay.</p>}
          </aside>
        </div>

        {showComparison && savedRuns.length >= 2 && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Saved run comparison">
          <div className="max-h-[85vh] w-full max-w-5xl overflow-auto border border-[#416357] bg-[#0c1714] p-5 shadow-2xl">
            <div className="mb-5 flex items-center justify-between"><h2 className="font-serif text-2xl">Saved run comparison</h2><button className="lab-button" onClick={() => setShowComparison(false)}>Close</button></div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{savedRuns.map((saved, index) => <div key={saved.replayDate} className="border border-[#294139] p-3"><p className="font-mono text-xs text-[#6fa78f]">RUN {index + 1} · #{saved.run.strategy_version}</p><p className="mt-2 text-lg">{saved.run.metrics.verdict}</p><div className="mt-3 space-y-2"><Row label="Replay date" value={new Date(saved.replayDate).toLocaleDateString()} /><Row label="Dataset" value={saved.run.dataset.asset} /><Row label="Partition" value={saved.run.dataset.research_period} /><Row label="Return" value={percent(saved.run.metrics.net_return_pct)} /><Row label="B&H" value={percent(saved.run.metrics.buy_and_hold_return_pct)} /><Row label="Drawdown" value={percent(saved.run.metrics.max_drawdown_pct)} /><Row label="Trades" value={String(saved.run.metrics.total_trades)} /></div><button className="lab-button mt-3 w-full" onClick={() => reopenRun(saved)}>Reopen</button></div>)}</div>
            <button className="lab-button mt-4" onClick={() => download("strategy-lab-comparison.json", JSON.stringify(savedRuns, null, 2), "application/json")}>Download comparison</button>
          </div>
        </div>}
        {showHelp && <HelpDialog onClose={() => setShowHelp(false)} />}
        {showUpload && <DatasetUploadDialog onClose={() => setShowUpload(false)} onCreated={(dataset) => { setDatasets((current) => [...current, dataset]); setDatasetId(dataset.id); setShowUpload(false); }} />}
      </div>
      <style jsx global>{`
        .strategy-lab { font-family: Georgia, "Times New Roman", serif; }
        .strategy-lab button, .strategy-lab input, .strategy-lab select, .strategy-lab table, .strategy-lab label { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: 0; }
        .lab-input { width: 100%; border: 1px solid #294139; border-radius: 2px; background: #08110f; padding: 7px 8px; color: #e6eee9; outline: none; }
        .lab-input:focus { border-color: #6fa78f; box-shadow: 0 0 0 1px #6fa78f; }
        .lab-button { min-height: 34px; border: 1px solid #416357; border-radius: 2px; background: #12231e; padding: 7px 10px; color: #dce7e1; font-size: 11px; text-transform: uppercase; }
        .lab-button:hover:not(:disabled) { background: #1a332a; border-color: #6fa78f; }
        .lab-button:disabled { cursor: not-allowed; opacity: .35; }
        .lab-segment { min-height: 34px; border: 1px solid #294139; border-radius: 2px; background: #08110f; font: 12px ui-monospace, monospace; color: #82978e; }
        .lab-segment-active { border-color: #6fa78f; background: #183228; color: #e6eee9; }
        .strategy-lab th, .strategy-lab td { padding: 8px 6px; white-space: nowrap; }
      `}</style>
    </>
  );
}

function SectionTitle({ index, title }: { index: string; title: string }) { return <div className="mb-3 flex items-center gap-2"><span className="font-mono text-[10px] text-[#6fa78f]">{index}</span><h2 className="font-mono text-xs uppercase text-[#dce7e1]">{title}</h2></div>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="mb-3 block text-xs text-[#aebdb7]"><span className="mb-1 block font-mono">{label}</span>{children}</label>; }
function Datum({ label, value }: { label: string; value: string }) { return <div><p className="text-[9px] uppercase text-[#82978e]">{label}</p><p className="mt-1 text-[#e6eee9]">{value}</p></div>; }
function Metric({ label, value }: { label: string; value: string }) { return <div className="bg-[#0c1714] p-3"><p className="font-mono text-[9px] uppercase text-[#82978e]">{label}</p><p className="mt-1 font-mono text-base">{value}</p></div>; }
function Row({ label, value }: { label: string; value: string }) { return <div className="flex items-center justify-between gap-3"><span className="text-[#82978e]">{label}</span><span className="text-right text-[#dce7e1]">{value}</span></div>; }
function ChartHeaderDatum({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) { return <div className="min-w-0 bg-[#0c1714] px-3 py-2"><p className="font-mono text-[8px] uppercase text-[#6f817a]">{label}</p><p className={`mt-1 truncate font-mono text-[10px] ${accent ? "text-[#31c48d]" : "text-[#dce7e1]"}`} title={value}>{value}</p></div>; }
function TransportButton({ label, symbol, onClick }: { label: string; symbol: string; onClick: () => void }) { return <button className="lab-segment min-h-8 text-[10px]" type="button" title={label} aria-label={label} onClick={onClick}>{symbol}</button>; }
function traceLabel(kind: ReplayResult["events"][number]["kind"], firstLimit = false) { return kind === "buy_limit" ? (firstLimit ? "BUY LIMIT" : "BUY LIMIT REPLACED") : kind === "cancelled_order" ? "BUY CANCELLED" : kind === "filled_order" ? "BUY FILLED" : kind === "entry" ? "POSITION OPENED" : kind === "protective_stop" ? "STOP INITIALIZED" : kind === "profit_activation" ? "PROFIT MODE" : kind === "trailing_floor" ? "TRAILING UPDATED" : "SELL FILLED"; }

function TradeSummary({ trade, timeline }: { trade: ReplayTrade; timeline: Array<{ event: ReplayResult["events"][number]; label: string }> }) {
  const replacements = timeline.filter((item) => item.label === "BUY LIMIT REPLACED").length;
  const trailingUpdates = timeline.filter((item) => item.label === "TRAILING UPDATED").length;
  const activated = timeline.some((item) => item.label === "PROFIT MODE");
  return <div className="grid grid-cols-2 gap-px bg-[#294139] sm:grid-cols-4">
    <DatumCell label="BUY LIMIT" value={`${replacements} replacements`} /><DatumCell label="Position" value={`Held ${trade.holding_candles} candles`} /><DatumCell label="Profit Mode" value={activated ? "Activated" : "Not activated"} /><DatumCell label="Trailing" value={`${trailingUpdates} updates`} /><DatumCell label="Exit" value={trade.exit_reason.replaceAll("_", " ")} /><DatumCell label="Net P&L" value={money(trade.net_pnl)} accent={number(trade.net_pnl) >= 0} />
  </div>;
}
function DatumCell({ label, value, accent }: { label: string; value: string; accent?: boolean }) { return <div className="bg-[#08110f] p-3"><p className="font-mono text-[9px] uppercase text-[#82978e]">{label}</p><p className={`mt-1 font-mono text-xs ${accent === undefined ? "text-[#dce7e1]" : accent ? "text-[#31c48d]" : "text-[#ef6b5f]"}`}>{value}</p></div>; }

const PATTERN_GROUPS: PatternFinding["group"][] = ["Price Structure", "Volatility", "Momentum", "Volume", "Breakouts", "Strategy Behavior"];

function PatternIntelligencePanel({ analysis, selected, onSelect }: { analysis: PatternAnalysis; selected: PatternFinding | null; onSelect: (finding: PatternFinding) => void }) {
  const cost = analysis.configuration.fee_pct !== undefined && analysis.configuration.slippage_pct !== undefined
    ? `fee ${analysis.configuration.fee_pct} + slippage ${analysis.configuration.slippage_pct} per side`
    : "configured replay costs";
  return <div className="mb-5 space-y-4" aria-label="Pattern Intelligence findings">
    <div className="flex justify-between font-mono text-[9px] text-[#82978e]"><span>{analysis.findings.length} findings</span><span>{number(analysis.elapsed_ms).toFixed(1)} ms</span></div>
    {PATTERN_GROUPS.map((group) => {
      const findings = analysis.findings.filter((item) => item.group === group);
      return <section key={group} aria-label={group}>
        <h3 className="mb-1 font-mono text-[10px] uppercase text-[#6fa78f]">{group} · {findings.length}</h3>
        {findings.length === 0 ? <p className="border-l border-[#294139] pl-2 font-mono text-[9px] text-[#6f817a]">No supported finding</p> : <div className="space-y-1">{findings.slice(0, 8).map((finding) => {
          const occurrence = finding.recurrence.find((item) => item.partition === "entire_dataset" && item.forward_horizon === 4);
          return <button key={finding.finding_id} type="button" onClick={() => onSelect(finding)} className={`w-full border p-2 text-left ${selected?.finding_id === finding.finding_id ? "border-[#58b8d8] bg-[#112a30]" : "border-[#294139] bg-[#08110f] hover:border-[#416357]"}`}>
            <span className="block font-mono text-[10px] text-[#dce7e1]">{finding.pattern_name}</span>
            <span className="mt-1 flex justify-between font-mono text-[8px] text-[#82978e]"><span>{finding.start_index}–{finding.end_index}</span><span>{occurrence?.occurrence_count ?? 0} matches · 4 bars</span></span>
            <span className={`mt-1 block font-mono text-[8px] ${finding.sufficient_evidence ? "text-[#31c48d]" : "text-[#e0b34c]"}`}>{finding.category.replaceAll("_", " ")}</span>
          </button>;
        })}{findings.length > 8 && <p className="border-l border-[#294139] py-1 pl-2 font-mono text-[8px] text-[#82978e]">{findings.length - 8} additional chart annotations</p>}</div>}
      </section>;
    })}
    {selected && <div className="border-l-2 border-[#58b8d8] bg-[#08110f] p-3">
      <p className="font-serif text-base text-[#e6eee9]">{selected.pattern_name}</p>
      <p className="mt-1 font-mono text-[8px] text-[#82978e]">{selected.detector_id} · v{selected.detector_version}</p>
      <p className="mt-3 font-mono text-[9px] uppercase text-[#6fa78f]">Why was this detected?</p>
      <ul className="mt-1 space-y-1">{selected.conditions.map((condition) => <li key={condition} className="font-mono text-[9px] leading-4 text-[#c7d4ce]">{condition}</li>)}</ul>
      <EvidenceMap title="Measurements" values={selected.measurements} />
      <EvidenceMap title="Thresholds" values={selected.thresholds} />
      <div className="mt-3 border-t border-[#294139] pt-2 font-mono text-[9px] leading-4 text-[#9fb0aa]">
        <p>Selected candles: {selected.start_index}–{selected.end_index}</p>
        <p>Partition: {analysis.partition.replaceAll("_", " ")}</p>
        <p>Cost assumption: {cost}</p>
        {[1, 2, 4, 8, 16].map((horizon) => {
          const recurrence = selected.recurrence.find((item) => item.partition === "entire_dataset" && item.forward_horizon === horizon);
          return <p key={horizon}>{horizon} bars · {recurrence?.occurrence_count ?? 0} matches · avg {recurrence?.average_forward_return === null || recurrence?.average_forward_return === undefined ? "n/a" : percent(number(recurrence.average_forward_return) * 100)} · net positive {recurrence?.net_positive_frequency === null || recurrence?.net_positive_frequency === undefined ? "n/a" : percent(number(recurrence.net_positive_frequency) * 100)}</p>;
        })}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-1 font-mono text-[8px] text-[#82978e]">{(["training", "validation", "final_test"] as const).map((partition) => {
        const evidence = selected.recurrence.find((item) => item.partition === partition && item.forward_horizon === 4);
        return <div key={partition} className="border border-[#294139] p-1.5"><span className="block uppercase">{partition.replace("_", " ")}</span><span className="mt-1 block text-[#dce7e1]">{evidence?.occurrence_count ?? 0} cases</span></div>;
      })}</div>
    </div>}
  </div>;
}

function EvidenceMap({ title, values }: { title: string; values: Record<string, string | number | boolean | null> }) {
  return <div className="mt-3"><p className="font-mono text-[9px] uppercase text-[#6fa78f]">{title}</p>{Object.entries(values).map(([key, value]) => <div key={key} className="mt-1 flex justify-between gap-2 font-mono text-[8px] text-[#9fb0aa]"><span>{key.replaceAll("_", " ")}</span><span className="text-right text-[#dce7e1]">{String(value)}</span></div>)}</div>;
}

function HelpDialog({ onClose }: { onClose: () => void }) {
  const metrics = [
    ["Net return", "Strategy capital change after fees and simulated slippage."], ["Buy & hold", "The same starting capital held in the asset over this replay period."], ["Economic value", "Ending trading capital plus withdrawals and tax reserve."], ["Outperformance", "Economic value minus the buy-and-hold ending value."], ["Win rate", "Completed trades with positive net P&L divided by all completed trades."], ["Max drawdown", "Largest peak-to-trough decline in simulated equity."], ["MFE / MAE", "Best favorable and worst adverse price excursion during one trade."],
  ];
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" role="dialog" aria-modal="true" aria-labelledby="strategy-lab-help-title">
    <div className="max-h-[88vh] w-full max-w-4xl overflow-auto border border-[#416357] bg-[#0c1714] p-5 shadow-2xl">
      <div className="flex items-start justify-between gap-4"><div><p className="font-mono text-[10px] uppercase text-[#6fa78f]">Chart field guide</p><h2 id="strategy-lab-help-title" className="mt-1 font-serif text-2xl">What am I looking at?</h2></div><button className="lab-button" type="button" onClick={onClose}>Close</button></div>
      <p className="mt-4 max-w-3xl text-sm leading-6 text-[#aebdb7]">This is a deterministic replay of historical candles. Every order, stop, activation, and exit comes from the Python engine trace. The browser explains and filters that evidence; it does not infer trades.</p>
      <h3 className="mt-6 font-mono text-xs uppercase text-[#dce7e1]">Price chart lines, markers, and colors</h3>
      <div className="mt-2 grid gap-px bg-[#294139] sm:grid-cols-2">{CHART_LAYERS.map((layer) => <div key={layer.id} className="flex gap-3 bg-[#08110f] p-3"><span className="w-5 text-center text-lg" style={{ color: layer.color }}>{layer.symbol}</span><div><p className="font-mono text-xs" style={{ color: layer.color }}>{layer.name}</p><p className="mt-1 text-xs leading-5 text-[#9fb0aa]">{layer.purpose}</p></div></div>)}</div>
      <h3 className="mt-6 font-mono text-xs uppercase text-[#dce7e1]">Trade timeline</h3>
      <p className="mt-2 text-xs leading-5 text-[#9fb0aa]">Select a completed ledger row or click inside a trade on the chart. The chart focuses its order-to-exit interval and reveals BUY placement, replacements, cancellations, fill, initialized stop, Profit Mode, changed trailing floors, and SELL exit in engine order.</p>
      <h3 className="mt-6 font-mono text-xs uppercase text-[#dce7e1]">Evidence metrics</h3>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">{metrics.map(([name, description]) => <div key={name} className="border-l-2 border-[#416357] pl-3"><p className="font-mono text-xs text-[#dce7e1]">{name}</p><p className="mt-1 text-xs leading-5 text-[#9fb0aa]">{description}</p></div>)}</div>
      <p className="mt-6 border-t border-[#294139] pt-4 font-mono text-[10px] leading-5 text-[#82978e]">All price objects use the right-hand price axis. Capital and equity are intentionally kept out of this price chart and reported separately in Evidence.</p>
    </div>
  </div>;
}
