"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/arena";
import { getExchangeConnections, type ExchangeConnection } from "@/lib/api/exchange-connections";
import {
  cancelCryptoOrderPreview,
  createCryptoOrderPreview,
  getCryptoOrderPreviewReadiness,
  listCryptoOrderPreviews,
  refreshCryptoOrderPreview,
  type CryptoOrderPreview,
  type CryptoOrderPreviewGeneratedBy,
  type CryptoOrderPreviewSide,
  type CryptoOrderPreviewStatus,
} from "@/lib/api/crypto-order-previews";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

function formatCurrency(value: string | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function formatNumber(value: string | null | undefined, digits = 8): string {
  if (value == null) {
    return "Not available";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(numeric);
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function statusClass(status: CryptoOrderPreviewStatus): string {
  if (status === "PREVIEW_READY") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "RISK_REJECTED" || status === "CONNECTION_NOT_READY" || status === "BALANCE_INSUFFICIENT" || status === "PREVIEW_FAILED") {
    return "border-rose-500/40 bg-rose-500/10 text-rose-100";
  }
  if (status === "EXPIRED") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-100";
  }
  if (status === "CANCELLED") {
    return "border-slate-500/40 bg-slate-500/10 text-slate-100";
  }
  return "border-cyan-500/40 bg-cyan-500/10 text-cyan-100";
}

function selectedConnectionLabel(connection: ExchangeConnection | undefined): string {
  if (!connection) {
    return "Select a Coinbase connection";
  }
  return `${connection.connection_name} · ${connection.environment} · ${connection.readiness.verdict}`;
}

export default function CryptoOrderPreviewCenter() {
  const [connections, setConnections] = useState<ExchangeConnection[]>([]);
  const [latestPreview, setLatestPreview] = useState<CryptoOrderPreview | null>(null);
  const [selectedConnectionId, setSelectedConnectionId] = useState<string>("");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [readiness, setReadiness] = useState<Awaited<ReturnType<typeof getCryptoOrderPreviewReadiness>> | null>(null);
  const [productId, setProductId] = useState("BTC-USD");
  const [side, setSide] = useState<CryptoOrderPreviewSide>("BUY");
  const [orderType] = useState("MARKET");
  const [quoteSize, setQuoteSize] = useState("5.00");
  const [requestedAmountCurrency, setRequestedAmountCurrency] = useState<"USD" | "BTC">("USD");
  const [decisionRecordId, setDecisionRecordId] = useState("");
  const [validationRunId, setValidationRunId] = useState("");
  const [strategyId, setStrategyId] = useState("");
  const [strategyName, setStrategyName] = useState("");
  const [generatedBy, setGeneratedBy] = useState<CryptoOrderPreviewGeneratedBy>("operator");
  const [clientRequestId, setClientRequestId] = useState("");
  const [selectedPreview, setSelectedPreview] = useState<CryptoOrderPreview | null>(null);

  const selectedConnection = useMemo(
    () => connections.find((item) => item.exchange_connection_id === selectedConnectionId),
    [connections, selectedConnectionId],
  );

  useEffect(() => {
    let active = true;

    async function load() {
      setLoadingConnections(true);
      setConnectionError(null);
      try {
        const [nextConnections, nextReadiness, nextPreviews] = await Promise.all([
          getExchangeConnections(),
          getCryptoOrderPreviewReadiness(),
          listCryptoOrderPreviews(10),
        ]);
        if (!active) {
          return;
        }
        setConnections(nextConnections.items.filter((item) => item.provider === "coinbase_advanced"));
        setReadiness(nextReadiness);
        setSelectedConnectionId((current) => current || nextConnections.items.find((item) => item.provider === "coinbase_advanced")?.exchange_connection_id || "");
        setLatestPreview(nextPreviews[0] ?? null);
        setSelectedPreview(nextPreviews[0] ?? null);
      } catch (requestError) {
        if (active) {
          setConnectionError(errorMessage(requestError, "Unable to load crypto order preview data."));
        }
      } finally {
        if (active) {
          setLoadingConnections(false);
        }
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (side === "BUY") {
      setRequestedAmountCurrency("USD");
      setQuoteSize((current) => current || "5.00");
    }
  }, [side]);

  const activePreview = latestPreview ?? selectedPreview;
  const noOrderCopy = "No order has been placed. This is an estimated preview only.";
  const chosenConnection = selectedConnection;
  const currentReadyText = chosenConnection ? chosenConnection.readiness.verdict : "UNKNOWN";

  async function handleGeneratePreview() {
    if (!chosenConnection) {
      setPreviewError("Select a Coinbase connection first.");
      return;
    }

    setGenerating(true);
    setPreviewError(null);
    setActionMessage(null);
    const requestId = clientRequestId.trim() || crypto.randomUUID();
    if (!clientRequestId.trim()) {
      setClientRequestId(requestId);
    }
    try {
      const created = await createCryptoOrderPreview({
        exchange_connection_id: chosenConnection.exchange_connection_id,
        environment: chosenConnection.environment,
        product_id: productId,
        side,
        order_type: "MARKET",
        quote_size: side === "BUY" ? quoteSize : null,
        base_size: null,
        requested_amount_currency: requestedAmountCurrency,
        decision_record_id: decisionRecordId.trim() || null,
        validation_run_id: validationRunId.trim() || null,
        strategy_id: strategyId.trim() || null,
        strategy_name: strategyName.trim() || null,
        generated_by: generatedBy,
        client_request_id: requestId,
      });
      setLatestPreview(created);
      setSelectedPreview(created);
      setActionMessage("Preview generated.");
    } catch (requestError) {
      setPreviewError(errorMessage(requestError, "Unable to generate preview."));
    } finally {
      setGenerating(false);
    }
  }

  async function handleRefreshPreview() {
    if (!activePreview) {
      return;
    }
    setRefreshing(true);
    setPreviewError(null);
    setActionMessage(null);
    try {
      const next = await refreshCryptoOrderPreview(activePreview.crypto_order_preview_id, { client_request_id: crypto.randomUUID() });
      setLatestPreview(next);
      setSelectedPreview(next);
      setActionMessage("Preview refreshed.");
    } catch (requestError) {
      setPreviewError(errorMessage(requestError, "Unable to refresh preview."));
    } finally {
      setRefreshing(false);
    }
  }

  async function handleCancelPreview() {
    if (!activePreview) {
      return;
    }
    if (!window.confirm("Cancel this preview?")) {
      return;
    }
    setCancelling(true);
    setPreviewError(null);
    setActionMessage(null);
    try {
      const next = await cancelCryptoOrderPreview(activePreview.crypto_order_preview_id, {
        reason: "Operator cancelled preview",
      });
      setLatestPreview(next);
      setSelectedPreview(next);
      setActionMessage("Preview cancelled.");
    } catch (requestError) {
      setPreviewError(errorMessage(requestError, "Unable to cancel preview."));
    } finally {
      setCancelling(false);
    }
  }

  const estimatedBalanceAfter = activePreview?.estimated_balance_after ?? null;
  const availableBefore = activePreview?.available_balance_before ?? null;
  const riskVerdict = activePreview?.risk_verdict?.toUpperCase().replaceAll("_", " ") ?? "UNKNOWN";
  const previewSummary = activePreview
    ? `${activePreview.side} ${activePreview.product_id} · ${formatCurrency(activePreview.requested_amount)}`
    : "No preview generated yet.";

  return (
    <div className="space-y-6 overflow-x-hidden">
      <header className="space-y-3 rounded-3xl border border-border/80 bg-slate-950/50 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">Crypto Order Preview</h1>
            <p className="mt-2 max-w-4xl text-sm text-foreground/75">
              Prepare a Coinbase Advanced spot trade, run OmniTrade safety checks, and inspect the official preview. No order is ever submitted.
            </p>
          </div>
          <Link href="/exchange-connections" className="rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-50 transition hover:bg-cyan-500/25">
            Return to Exchange Connections
          </Link>
        </div>
        <div className="rounded-2xl border border-amber-400/40 bg-amber-500/10 p-3 text-sm text-amber-50">
          {noOrderCopy}
        </div>
      </header>

      {connectionError ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          {connectionError}
        </section>
      ) : null}

      {previewError ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          {previewError}
        </section>
      ) : null}

      {actionMessage ? (
        <section className="rounded-2xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">
          {actionMessage}
        </section>
      ) : null}

      {loadingConnections ? (
        <section className="rounded-2xl border border-border bg-background/60 p-3 text-sm text-foreground/75">Loading preview workspace...</section>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <section className="rounded-3xl border border-border/80 bg-slate-950/40 p-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <label className="text-sm lg:col-span-2">
              <span className="mb-1 block text-foreground/80">Connection</span>
              <select className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={selectedConnectionId} onChange={(event) => setSelectedConnectionId(event.target.value)}>
                <option value="">Select Coinbase connection</option>
                {connections.map((item) => (
                  <option key={item.exchange_connection_id} value={item.exchange_connection_id}>
                    {item.connection_name} · {item.environment} · {item.readiness.verdict}
                  </option>
                ))}
              </select>
            </label>

            <article className="rounded-2xl border border-border bg-background/50 p-4 text-sm lg:col-span-2">
              <p className="text-[11px] uppercase tracking-wide text-foreground/65">Step 1 · Select Connection</p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <p>Readiness: {chosenConnection ? chosenConnection.readiness.verdict : readiness?.ready ? "READY" : "UNKNOWN"}</p>
                <p>Last verified: {formatTimestamp(chosenConnection?.readiness.checked_at)}</p>
                <p>Environment: {chosenConnection?.environment ?? "Not selected"}</p>
                <p>Current status: {chosenConnection?.status ?? "Not selected"}</p>
                <p>Available USD: {formatCurrency(chosenConnection?.balances.find((item) => item.currency === "USD")?.available)}</p>
                <p>Available BTC: {formatNumber(chosenConnection?.balances.find((item) => item.currency === "BTC")?.available)}</p>
              </div>
            </article>

            <article className="rounded-2xl border border-border bg-background/50 p-4 text-sm lg:col-span-2">
              <p className="text-[11px] uppercase tracking-wide text-foreground/65">Step 2 · Configure Preview</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Product</span>
                  <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={productId} onChange={(event) => setProductId(event.target.value.toUpperCase())} />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Side</span>
                  <select className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={side} onChange={(event) => setSide(event.target.value as CryptoOrderPreviewSide)}>
                    <option value="BUY">BUY</option>
                    <option value="SELL">SELL</option>
                  </select>
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Order Type</span>
                  <input className="w-full rounded-md border border-border bg-background/50 px-3 py-2" value={orderType} readOnly />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Amount Currency</span>
                  <select className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={requestedAmountCurrency} onChange={(event) => setRequestedAmountCurrency(event.target.value as "USD" | "BTC") }>
                    <option value="USD">USD</option>
                    <option value="BTC">BTC</option>
                  </select>
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Quote Size</span>
                  <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={quoteSize} onChange={(event) => setQuoteSize(event.target.value)} />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Generated By</span>
                  <select className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={generatedBy} onChange={(event) => setGeneratedBy(event.target.value as CryptoOrderPreviewGeneratedBy)}>
                    <option value="operator">operator</option>
                    <option value="system_recommendation">system_recommendation</option>
                  </select>
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Decision Record ID</span>
                  <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={decisionRecordId} onChange={(event) => setDecisionRecordId(event.target.value)} placeholder="Optional" />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Validation Run ID</span>
                  <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={validationRunId} onChange={(event) => setValidationRunId(event.target.value)} placeholder="Optional" />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-foreground/80">Strategy ID</span>
                  <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={strategyId} onChange={(event) => setStrategyId(event.target.value)} placeholder="Optional" />
                </label>
                <label className="text-sm sm:col-span-2">
                  <span className="mb-1 block text-foreground/80">Strategy Name</span>
                  <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={strategyName} onChange={(event) => setStrategyName(event.target.value)} placeholder="Optional" />
                </label>
              </div>
            </article>

            <article className="rounded-2xl border border-border bg-background/50 p-4 text-sm lg:col-span-2">
              <p className="text-[11px] uppercase tracking-wide text-foreground/65">Step 3 · Review Safety</p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                <p>Connection ready: {currentReadyText}</p>
                <p>Small Account Max: {readiness ? formatCurrency(readiness.max_quote_size_usd) : "$25.00"}</p>
                <p>Default Preview: {readiness ? formatCurrency(readiness.default_quote_size_usd) : "$5.00"}</p>
                <p>Market freshness: {readiness?.market_data_max_age_minutes ?? 15}m</p>
                <p>Available balance before: {availableBefore ? formatCurrency(availableBefore) : "Not available"}</p>
                <p>Risk verdict: {riskVerdict}</p>
              </div>
              <div className="mt-3 rounded-xl border border-border bg-background/40 p-3 text-xs text-foreground/75">
                Preview-only safety checks run before the Coinbase request. The Risk Engine can approve only for preview, never for execution.
              </div>
            </article>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-md border border-cyan-400/40 bg-cyan-500/20 px-4 py-2 text-sm font-semibold text-cyan-50"
              onClick={() => void handleGeneratePreview()}
              disabled={generating || !chosenConnection}
            >
              {generating ? "Generating preview…" : "Generate Preview"}
            </button>
            <button
              type="button"
              className="rounded-md border border-emerald-400/40 bg-emerald-500/20 px-4 py-2 text-sm font-semibold text-emerald-50"
              onClick={() => void handleRefreshPreview()}
              disabled={refreshing || activePreview == null}
            >
              {refreshing ? "Refreshing…" : "Refresh Preview"}
            </button>
            <button
              type="button"
              className="rounded-md border border-rose-400/40 bg-rose-500/20 px-4 py-2 text-sm font-semibold text-rose-50"
              onClick={() => void handleCancelPreview()}
              disabled={cancelling || activePreview == null}
            >
              {cancelling ? "Cancelling…" : "Cancel Preview"}
            </button>
            <Link href="/risk-monitor" className="rounded-md border border-slate-400/40 bg-slate-500/20 px-4 py-2 text-sm font-semibold text-slate-50">
              View Risk Details
            </Link>
            {activePreview?.decision_record_id ? (
              <Link href={`/dashboard/decisions?decision_id=${activePreview.decision_record_id}`} className="rounded-md border border-violet-400/40 bg-violet-500/20 px-4 py-2 text-sm font-semibold text-violet-50">
                View Decision Record
              </Link>
            ) : (
              <span className="rounded-md border border-border bg-background/45 px-4 py-2 text-sm text-foreground/45">View Decision Record</span>
            )}
          </div>
        </section>

        <aside className="space-y-4">
          <section className="rounded-3xl border border-border/80 bg-slate-950/40 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Preview Result</h2>
            <div className="mt-3 rounded-2xl border border-amber-400/40 bg-amber-500/10 p-3 text-sm text-amber-50">
              {noOrderCopy}
            </div>
            {activePreview ? (
              <article className={`mt-3 rounded-3xl border p-4 ${statusClass(activePreview.status)}`}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs uppercase tracking-wide opacity-75">{activePreview.side} {activePreview.product_id}</p>
                    <h3 className="mt-1 text-xl font-semibold">{activePreview.product_id}</h3>
                    <p className="mt-1 text-sm opacity-80">Status: {activePreview.status}</p>
                  </div>
                  <span className="rounded-full border border-current/40 px-3 py-1 text-xs font-semibold">{activePreview.risk_verdict?.toUpperCase().replaceAll("_", " ") ?? "UNKNOWN"}</span>
                </div>

                <div className="mt-4 grid gap-2 text-sm">
                  <p>Requested: {formatCurrency(activePreview.requested_amount)}</p>
                  <p>Estimated BTC: {formatNumber(activePreview.estimated_base_size)}</p>
                  <p>Estimated Average Price: {formatCurrency(activePreview.estimated_average_price)}</p>
                  <p>Estimated Coinbase Fee: {formatCurrency(activePreview.estimated_fee)}</p>
                  <p>Estimated Total: {formatCurrency(activePreview.estimated_total_value)}</p>
                  <p>Available USD Before: {formatCurrency(activePreview.available_balance_before)}</p>
                  <p>Estimated USD After: {formatCurrency(activePreview.estimated_balance_after)}</p>
                  <p>Expires: {formatTimestamp(activePreview.expires_at)}</p>
                </div>

                <div className="mt-4 rounded-xl border border-border bg-background/35 p-3 text-sm">
                  <p className="font-semibold">Warnings</p>
                  <div className="mt-2 space-y-1 text-xs text-foreground/80">
                    {activePreview.warning_messages.length > 0 ? activePreview.warning_messages.map((item) => <p key={item}>{item}</p>) : <p>None</p>}
                  </div>
                </div>

                <div className="mt-4 rounded-xl border border-border bg-background/35 p-3 text-xs text-foreground/75">
                  <p>Connection: {activePreview.readiness_verdict ?? "UNKNOWN"}</p>
                  <p>Preview ID: {activePreview.preview_id ?? "Not provided"}</p>
                  <p>Generated by: {activePreview.generated_by}</p>
                </div>
              </article>
            ) : (
              <div className="mt-3 rounded-xl border border-border bg-background/40 p-4 text-sm text-foreground/70">
                Generate a preview to inspect Coinbase estimates, risk disposition, and remaining balance.
              </div>
            )}
            <div className="mt-3 rounded-xl border border-border bg-background/40 p-3 text-xs text-foreground/75">
              Live order submission is not implemented.
            </div>
          </section>

          <section className="rounded-3xl border border-border/80 bg-slate-950/40 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Latest Preview Evidence</h2>
            <div className="mt-3 space-y-2">
              {latestPreview ? (
                <div className="rounded-2xl border border-border bg-background/45 p-3 text-sm">
                  <p>{previewSummary}</p>
                  <p className="mt-1 text-xs text-foreground/65">Expires: {formatTimestamp(latestPreview.expires_at)}</p>
                  <p className="mt-1 text-xs text-foreground/65">Age: {formatTimestamp(latestPreview.created_at)}</p>
                </div>
              ) : (
                <div className="rounded-2xl border border-border bg-background/45 p-3 text-sm text-foreground/70">
                  No preview evidence yet.
                </div>
              )}
            </div>
          </section>
        </aside>
      </div>

      <footer className="rounded-3xl border border-amber-400/40 bg-amber-500/10 p-4 text-sm text-amber-50">
        {noOrderCopy}
      </footer>
    </div>
  );
}
