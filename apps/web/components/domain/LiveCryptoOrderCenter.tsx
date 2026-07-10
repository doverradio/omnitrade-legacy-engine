"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/live";
import {
  cancelLiveCryptoOrder,
  dryRunLiveCryptoOrderConfirmation,
  getLiveCryptoOrderReadiness,
  listLiveCryptoOrders,
  prepareLiveCryptoOrderConfirmation,
  reconcileLiveCryptoOrder,
  submitLiveCryptoOrder,
  type LiveCryptoOrder,
  type LiveCryptoOrderDryRunResponse,
  type LiveCryptoOrderPrepareResponse,
  type LiveCryptoOrderReadiness,
  type LiveCryptoOrderStatus,
} from "@/lib/api/live-crypto-orders";

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

function statusClass(status: LiveCryptoOrderStatus): string {
  if (status === "FILLED") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "REJECTED" || status === "RISK_REJECTED") {
    return "border-rose-500/40 bg-rose-500/10 text-rose-100";
  }
  if (status === "CANCELLED") {
    return "border-slate-500/40 bg-slate-500/10 text-slate-100";
  }
  if (status === "RECONCILIATION_REQUIRED") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-100";
  }
  if (status === "DRY_RUN_READY") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "DRY_RUN_BLOCKED") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-100";
  }
  return "border-cyan-500/40 bg-cyan-500/10 text-cyan-100";
}

export default function LiveCryptoOrderCenter() {
  const [profileId, setProfileId] = useState("");
  const [previewId, setPreviewId] = useState("");
  const [operatorIdentity, setOperatorIdentity] = useState("operator:human");
  const [confirmationPhrase, setConfirmationPhrase] = useState("BUY BTC");
  const [idempotencyToken, setIdempotencyToken] = useState(() => crypto.randomUUID());
  const [readiness, setReadiness] = useState<LiveCryptoOrderReadiness | null>(null);
  const [orders, setOrders] = useState<LiveCryptoOrder[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<LiveCryptoOrder | null>(null);
  const [preparedConfirmation, setPreparedConfirmation] = useState<LiveCryptoOrderPrepareResponse | null>(null);
  const [dryRunResult, setDryRunResult] = useState<LiveCryptoOrderDryRunResponse | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [dryRunning, setDryRunning] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const fixedOrderSummary = useMemo(
    () => ["Coinbase Advanced", "production", "BTC-USD", "BUY", "MARKET", "$5 max"].join(" · "),
    [],
  );

  const canSubmit = readiness?.feature_flag_enabled === true && readiness?.live_mode_enabled === true && readiness?.live_profile_ready === true;
  const canDryRun = readiness?.dry_run_enabled === true && profileId.trim().length > 0 && previewId.trim().length > 0;

  async function loadWorkspace() {
    if (!profileId.trim()) {
      setError("Enter a live trading profile ID first.");
      return;
    }
    setLoading(true);
    setError(null);
    setStatusMessage(null);
    try {
      const [nextReadiness, nextOrders] = await Promise.all([
        getLiveCryptoOrderReadiness(profileId.trim()),
        listLiveCryptoOrders(profileId.trim()),
      ]);
      setReadiness(nextReadiness);
      setOrders(nextOrders);
      setSelectedOrder(nextOrders[0] ?? null);
      setStatusMessage(nextReadiness.feature_flag_enabled ? "Live workspace loaded." : "Live submission is disabled by server flag.");
    } catch (requestError) {
      setError(errorMessage(requestError, "Unable to load live order workspace."));
    } finally {
      setLoading(false);
    }
  }

  async function runDryRun() {
    if (!canDryRun) {
      setError("Dry run requires readiness to be loaded and the server dry-run flag to be enabled.");
      return;
    }
    setDryRunning(true);
    setError(null);
    setStatusMessage(null);
    try {
      const response = await dryRunLiveCryptoOrderConfirmation({
        live_trading_profile_id: profileId.trim(),
        crypto_order_preview_id: previewId.trim(),
        operator_identity: operatorIdentity.trim(),
        idempotency_token: idempotencyToken.trim() || crypto.randomUUID(),
      });
      setDryRunResult(response);
      setSelectedOrder(response.live_crypto_order);
      setOrders((current) => {
        const existing = current.filter((item) => item.live_crypto_order_id !== response.live_crypto_order.live_crypto_order_id);
        return [response.live_crypto_order, ...existing];
      });
      setStatusMessage(response.order_submitted ? "Dry run unexpectedly submitted an order." : response.dry_run_message);
    } catch (requestError) {
      setError(errorMessage(requestError, "Unable to run live order dry run."));
    } finally {
      setDryRunning(false);
    }
  }

  async function prepareConfirmation() {
    if (!canSubmit) {
      setError("Live submission is disabled until the server gate is enabled and the profile is live-ready.");
      return;
    }
    if (!profileId.trim() || !previewId.trim()) {
      setError("Enter both the live trading profile ID and the preview ID.");
      return;
    }
    setPreparing(true);
    setError(null);
    setStatusMessage(null);
    try {
      const response = await prepareLiveCryptoOrderConfirmation({
        live_trading_profile_id: profileId.trim(),
        crypto_order_preview_id: previewId.trim(),
        operator_identity: operatorIdentity.trim(),
        idempotency_token: idempotencyToken.trim() || crypto.randomUUID(),
      });
      setPreparedConfirmation(response);
      setSelectedOrder(response.live_crypto_order);
      setOrders((current) => {
        const existing = current.filter((item) => item.live_crypto_order_id !== response.live_crypto_order.live_crypto_order_id);
        return [response.live_crypto_order, ...existing];
      });
      setStatusMessage(`Confirmation prepared. Challenge ${response.confirmation_challenge_id}.`);
    } catch (requestError) {
      setError(errorMessage(requestError, "Unable to prepare live order confirmation."));
    } finally {
      setPreparing(false);
    }
  }

  async function submitOrder() {
    if (!selectedOrder) {
      setError("Prepare a live order before submitting.");
      return;
    }
    if (!canSubmit) {
      setError("Live submission is disabled by server gate.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setStatusMessage(null);
    try {
      const response = await submitLiveCryptoOrder({
        live_crypto_order_id: selectedOrder.live_crypto_order_id,
        confirmation_challenge_id: preparedConfirmation?.confirmation_challenge_id ?? selectedOrder.audit_correlation_id,
        confirmation_phrase: confirmationPhrase.trim(),
        operator_identity: operatorIdentity.trim(),
        idempotency_token: idempotencyToken.trim() || crypto.randomUUID(),
      });
      setSelectedOrder(response.live_crypto_order);
      setOrders((current) => current.map((item) => (item.live_crypto_order_id === response.live_crypto_order.live_crypto_order_id ? response.live_crypto_order : item)));
      setStatusMessage(response.order_submitted ? "Live order submitted." : "Live order submission failed and requires reconciliation.");
    } catch (requestError) {
      setError(errorMessage(requestError, "Unable to submit live order."));
    } finally {
      setSubmitting(false);
    }
  }

  async function reconcileOrder() {
    if (!selectedOrder) {
      return;
    }
    setReconciling(true);
    setError(null);
    setStatusMessage(null);
    try {
      const response = await reconcileLiveCryptoOrder(selectedOrder.live_crypto_order_id, {
        operator_identity: operatorIdentity.trim(),
      });
      setSelectedOrder(response.live_crypto_order);
      setOrders((current) => current.map((item) => (item.live_crypto_order_id === response.live_crypto_order.live_crypto_order_id ? response.live_crypto_order : item)));
      setStatusMessage(`Reconciliation status: ${response.reconciliation_status}.`);
    } catch (requestError) {
      setError(errorMessage(requestError, "Unable to reconcile live order."));
    } finally {
      setReconciling(false);
    }
  }

  async function cancelOrder() {
    if (!selectedOrder) {
      return;
    }
    setCancelling(true);
    setError(null);
    setStatusMessage(null);
    try {
      const response = await cancelLiveCryptoOrder(selectedOrder.live_crypto_order_id, {
        reason: "Operator requested cancel",
        operator_identity: operatorIdentity.trim(),
      });
      setSelectedOrder(response);
      setOrders((current) => current.map((item) => (item.live_crypto_order_id === response.live_crypto_order_id ? response : item)));
      setStatusMessage("Live order cancel requested.");
    } catch (requestError) {
      setError(errorMessage(requestError, "Unable to cancel live order."));
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-3 rounded-3xl border border-rose-400/30 bg-slate-950/70 p-5 shadow-[0_20px_80px_rgba(0,0,0,0.3)]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-[0.3em] text-rose-200/80">LIVE MONEY</p>
            <h1 className="mt-2 text-3xl font-semibold text-foreground">Live Crypto Orders</h1>
            <p className="mt-2 max-w-3xl text-sm text-foreground/75">
              Prepare a human-confirmed Coinbase Advanced BTC-USD market buy, then explicitly submit it. The backend blocks this flow until the live flag is enabled.
            </p>
          </div>
          <Link href="/live-trading" className="rounded-full border border-border bg-background/50 px-4 py-2 text-sm font-semibold text-foreground transition hover:bg-background/80">
            Back to Live Trading Ops
          </Link>
        </div>
        <div className="rounded-2xl border border-amber-400/40 bg-amber-500/10 p-3 text-sm text-amber-50">
          {fixedOrderSummary}
        </div>
        <div className="rounded-2xl border border-rose-400/40 bg-rose-500/10 p-3 text-sm text-rose-50">
          Operator confirmation is mandatory for every order. The default server state is closed.
        </div>
      </header>

      {error ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          {error}
        </section>
      ) : null}

      {statusMessage ? (
        <section className="rounded-2xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">
          {statusMessage}
        </section>
      ) : null}

      <section className="grid gap-4 lg:grid-cols-[1.05fr_0.95fr]">
        <article className="rounded-3xl border border-border/80 bg-slate-950/40 p-5">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm md:col-span-2">
              <span className="mb-1 block text-foreground/80">Live Trading Profile ID</span>
              <input
                className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                value={profileId}
                onChange={(event) => setProfileId(event.target.value)}
                placeholder="Enter live_trading_profile_id"
              />
            </label>
            <label className="text-sm md:col-span-2">
              <span className="mb-1 block text-foreground/80">Preview ID</span>
              <input
                className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                value={previewId}
                onChange={(event) => setPreviewId(event.target.value)}
                placeholder="Enter approved preview ID"
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-foreground/80">Operator Identity</span>
              <input
                className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                value={operatorIdentity}
                onChange={(event) => setOperatorIdentity(event.target.value)}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-foreground/80">Idempotency Token</span>
              <input
                className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                value={idempotencyToken}
                onChange={(event) => setIdempotencyToken(event.target.value)}
              />
            </label>
            <label className="text-sm md:col-span-2">
              <span className="mb-1 block text-foreground/80">Typed Confirmation Phrase</span>
              <input
                className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                value={confirmationPhrase}
                onChange={(event) => setConfirmationPhrase(event.target.value)}
              />
            </label>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-50 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={loadWorkspace}
              disabled={loading}
            >
              {loading ? "Loading..." : "Load Readiness"}
            </button>
            <button
              type="button"
              className="rounded-full border border-amber-400/40 bg-amber-500/15 px-4 py-2 text-sm font-semibold text-amber-50 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={prepareConfirmation}
              disabled={preparing || !canSubmit}
            >
              {preparing ? "Preparing..." : "Prepare Confirmation"}
            </button>
            <button
              type="button"
              className="rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-50 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={runDryRun}
              disabled={dryRunning || !canDryRun}
            >
              {dryRunning ? "Running Dry Run..." : "Run Dry Run"}
            </button>
            <button
              type="button"
              className="rounded-full border border-emerald-400/40 bg-emerald-500/15 px-4 py-2 text-sm font-semibold text-emerald-50 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={submitOrder}
              disabled={submitting || !canSubmit || !selectedOrder || !preparedConfirmation}
            >
              {submitting ? "Submitting..." : "Submit Live Order"}
            </button>
            <button
              type="button"
              className="rounded-full border border-slate-400/40 bg-slate-500/15 px-4 py-2 text-sm font-semibold text-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={reconcileOrder}
              disabled={reconciling || !selectedOrder}
            >
              {reconciling ? "Reconciling..." : "Reconcile"}
            </button>
            <button
              type="button"
              className="rounded-full border border-rose-400/40 bg-rose-500/15 px-4 py-2 text-sm font-semibold text-rose-50 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={cancelOrder}
              disabled={cancelling || !selectedOrder}
            >
              {cancelling ? "Cancelling..." : "Cancel"}
            </button>
          </div>
        </article>

        <article className="rounded-3xl border border-border/80 bg-slate-950/40 p-5">
          <p className="text-xs uppercase tracking-[0.3em] text-foreground/60">Server Gate</p>
          <h2 className="mt-2 text-xl font-semibold text-foreground">Readiness</h2>
          <div className="mt-4 space-y-3 text-sm text-foreground/80">
            <p>Overall verdict: {readiness?.overall_verdict ?? "Not loaded"}</p>
            <p>Feature flag: {readiness ? (readiness.feature_flag_enabled ? "Enabled" : "Disabled") : "Not loaded"}</p>
            <p>Dry run: {readiness ? (readiness.dry_run_enabled ? "Enabled" : "Disabled") : "Not loaded"}</p>
            <p>Live profile ready: {readiness ? String(readiness.live_profile_ready) : "Not loaded"}</p>
            <p>Live mode enabled: {readiness ? String(readiness.live_mode_enabled) : "Not loaded"}</p>
            <p>Max order size: {readiness ? formatCurrency(readiness.max_order_usd) : "$5.00"}</p>
            <p>Latest preview age: {readiness?.latest_preview_age_seconds ?? "Not available"}</p>
            <p>Gate reason: {readiness?.reason ?? "None"}</p>
          </div>
          {readiness?.checks?.length ? (
            <div className="mt-4 space-y-2 rounded-2xl border border-border bg-background/40 p-3 text-sm text-foreground/80">
              <p className="text-xs uppercase tracking-[0.3em] text-foreground/60">First Live Trade Readiness</p>
              {readiness.checks.map((check) => (
                <div key={check.code} className="rounded-xl border border-border/80 bg-slate-950/40 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold text-foreground">{check.label}</p>
                    <span className="text-xs uppercase tracking-[0.25em] text-foreground/60">{check.status}</span>
                  </div>
                  <p className="mt-1 text-xs text-foreground/70">{check.explanation}</p>
                  <p className="mt-1 text-xs text-foreground/60">{check.remediation}</p>
                </div>
              ))}
            </div>
          ) : null}
          <div className="mt-4 rounded-2xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-50">
            If the server flag is disabled, the UI refuses submission and keeps the control path closed.
          </div>
          {dryRunResult ? (
            <div className="mt-3 rounded-2xl border border-cyan-500/40 bg-cyan-500/10 p-3 text-sm text-cyan-50">
              <p>Dry run status: {dryRunResult.dry_run_status}</p>
              <p className="mt-1">{dryRunResult.dry_run_message}</p>
              <p className="mt-1">Coinbase Create Order called: {dryRunResult.provider_create_order_called ? "Yes" : "No"}</p>
            </div>
          ) : null}
          {preparedConfirmation ? (
            <div className="mt-3 rounded-2xl border border-cyan-500/40 bg-cyan-500/10 p-3 text-sm text-cyan-50">
              <p>Confirmation required: {preparedConfirmation.confirmation_phrase_required}</p>
              <p className="mt-1">Expires: {formatTimestamp(preparedConfirmation.confirmation_expires_at)}</p>
              <p className="mt-1">Warning: {preparedConfirmation.live_money_warning}</p>
            </div>
          ) : null}
        </article>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <article className="rounded-3xl border border-border/80 bg-slate-950/40 p-5">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xl font-semibold text-foreground">Recent Live Orders</h2>
            <span className="text-xs uppercase tracking-[0.3em] text-foreground/60">{orders.length} items</span>
          </div>
          <div className="mt-4 space-y-3">
            {orders.length === 0 ? <p className="text-sm text-foreground/70">No live orders loaded yet.</p> : null}
            {orders.map((order) => (
              <button
                key={order.live_crypto_order_id}
                type="button"
                onClick={() => setSelectedOrder(order)}
                className="block w-full rounded-2xl border border-border bg-background/50 p-3 text-left transition hover:bg-background/70"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="font-semibold text-foreground">{order.product_id} · {order.side} · {formatCurrency(order.requested_quote_size)}</p>
                  <span className={`rounded-full border px-2 py-0.5 text-[11px] uppercase tracking-wide ${statusClass(order.status)}`}>
                    {order.status}
                  </span>
                </div>
                <p className="mt-1 text-xs text-foreground/65">Provider: {order.provider} · Created: {formatTimestamp(order.created_at)}</p>
              </button>
            ))}
          </div>
        </article>

        <article className="rounded-3xl border border-border/80 bg-slate-950/40 p-5">
          <p className="text-xs uppercase tracking-[0.3em] text-foreground/60">Selected Order</p>
          {selectedOrder ? (
            <div className="mt-3 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-xl font-semibold text-foreground">{selectedOrder.product_id}</h2>
                <span className={`rounded-full border px-2 py-0.5 text-[11px] uppercase tracking-wide ${statusClass(selectedOrder.status)}`}>
                  {selectedOrder.status}
                </span>
              </div>
              <div className="grid gap-2 text-sm text-foreground/80 sm:grid-cols-2">
                <p>Live order ID: {selectedOrder.live_crypto_order_id}</p>
                <p>Preview ID: {selectedOrder.crypto_order_preview_id}</p>
                <p>Provider order ID: {selectedOrder.provider_order_id ?? "Not submitted"}</p>
                <p>Provider status: {selectedOrder.provider_status ?? "Unknown"}</p>
                <p>Requested size: {formatCurrency(selectedOrder.requested_quote_size)}</p>
                <p>Submitted at: {formatTimestamp(selectedOrder.submitted_at)}</p>
                <p>Filled at: {formatTimestamp(selectedOrder.filled_at)}</p>
                <p>Cancelled at: {formatTimestamp(selectedOrder.cancelled_at)}</p>
              </div>
              <div className="rounded-2xl border border-border bg-background/50 p-3 text-xs text-foreground/75">
                <p>Execution verdict: {typeof selectedOrder.safe_provider_response["execution_risk_verdict"] === "string" ? selectedOrder.safe_provider_response["execution_risk_verdict"] : "Not available"}</p>
                <p className="mt-1 break-all">Client order ID: {selectedOrder.client_order_id}</p>
                <p className="mt-1 break-all">Failure reason: {selectedOrder.failure_reason ?? "None"}</p>
              </div>
            </div>
          ) : (
            <p className="mt-3 text-sm text-foreground/70">Prepare a live order to inspect confirmation and execution state.</p>
          )}
        </article>
      </section>
    </div>
  );
}
