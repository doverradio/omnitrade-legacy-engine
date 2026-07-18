"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/live";
import { getExchangeConnections, type ExchangeConnection } from "@/lib/api/exchange-connections";
import {
  adoptInstantTrade,
  buyInstantTrade,
  getInstantTrade,
  type InstantTradeReceipt,
  type InstantTradeStatus,
} from "@/lib/api/instant-trades";

function formatCurrency(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

const PRODUCT_OPTIONS = ["BTC-USD", "ETH-USD", "SOL-USD"];
const PRICE_HINTS: Record<string, number> = {
  "BTC-USD": 100000,
  "ETH-USD": 5000,
  "SOL-USD": 200,
};

export default function InstantBuyCard() {
  const [connections, setConnections] = useState<ExchangeConnection[]>([]);
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [paperAccountId, setPaperAccountId] = useState("");
  const [liveProfileId, setLiveProfileId] = useState("");
  const [product, setProduct] = useState("BTC-USD");
  const [quoteAmount, setQuoteAmount] = useState("5.00");
  const [actor, setActor] = useState("operator:human");
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [confirmation, setConfirmation] = useState(false);

  const [status, setStatus] = useState<InstantTradeStatus | "IDLE">("IDLE");
  const [receipt, setReceipt] = useState<InstantTradeReceipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [polling, setPolling] = useState(false);
  const [submittedAtMs, setSubmittedAtMs] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    async function loadConnections() {
      setLoading(true);
      try {
        const response = await getExchangeConnections();
        if (!active) {
          return;
        }
        const eligible = response.items.filter((item) => item.provider === "kraken_spot" && item.environment === "production");
        setConnections(eligible);
        setSelectedConnectionId(eligible[0]?.exchange_connection_id ?? "");
      } catch (requestError) {
        if (active) {
          setError(errorMessage(requestError, "Unable to load connected accounts."));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }
    void loadConnections();
    return () => {
      active = false;
    };
  }, []);

  const selectedConnection = useMemo(
    () => connections.find((item) => item.exchange_connection_id === selectedConnectionId) ?? null,
    [connections, selectedConnectionId],
  );

  const estimatedQuantity = useMemo(() => {
    const amount = Number(quoteAmount);
    const price = PRICE_HINTS[product] ?? 1;
    if (!Number.isFinite(amount) || amount <= 0) {
      return "0";
    }
    return (amount / price).toFixed(8);
  }, [product, quoteAmount]);

  const estimatedFees = useMemo(() => {
    const amount = Number(quoteAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      return "$0.00";
    }
    return formatCurrency((amount * 0.004).toFixed(2));
  }, [quoteAmount]);

  useEffect(() => {
    if (!polling || !receipt) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const next = await getInstantTrade(receipt.internal_order_id);
        setReceipt(next);
        setStatus(next.status);
        if (["FILLED", "REJECTED", "FAILED"].includes(next.status)) {
          setPolling(false);
        }
      } catch (requestError) {
        setError(errorMessage(requestError, "Unable to refresh instant order status."));
      }
    }, 2000);

    return () => window.clearInterval(timer);
  }, [polling, receipt]);

  const showLongPendingMessage =
    polling &&
    submittedAtMs != null &&
    Date.now() - submittedAtMs >= 10000;

  async function onBuyNow() {
    if (!selectedConnection) {
      setError("Select a connected production Kraken account first.");
      return;
    }
    if (!confirmation) {
      setError("Explicit confirmation is required.");
      return;
    }

    setError(null);
    setMessage(null);
    setStatus("VALIDATING");

    try {
      setStatus("SUBMITTING");
      const next = await buyInstantTrade({
        paper_account_id: paperAccountId.trim(),
        live_trading_profile_id: liveProfileId.trim(),
        provider: selectedConnection.provider,
        environment: selectedConnection.environment,
        product,
        quote_amount: quoteAmount,
        actor: actor.trim(),
        confirmation,
        idempotency_key: idempotencyKey.trim(),
      });
      setReceipt(next);
      setStatus(next.status);
      setSubmittedAtMs(Date.now());

      if (["PENDING", "RECONCILIATION_REQUIRED"].includes(next.status)) {
        setPolling(true);
        setMessage("Order persisted. Polling for provider reconciliation...");
      } else {
        setPolling(false);
      }
    } catch (requestError) {
      setStatus("FAILED");
      setPolling(false);
      setError(errorMessage(requestError, "Instant buy failed."));
    }
  }

  async function onAdopt() {
    if (!receipt) {
      return;
    }
    try {
      const next = await adoptInstantTrade(receipt.internal_order_id, actor.trim());
      setReceipt(next);
      setMessage("Order was marked for autonomous management adoption.");
    } catch (requestError) {
      setError(errorMessage(requestError, "Adoption failed."));
    }
  }

  return (
    <section className="max-w-xl space-y-4 rounded-3xl border border-border bg-slate-950/50 p-5">
      <header>
        <p className="text-xs uppercase tracking-[0.3em] text-cyan-200/80">User Directed Instant Trade</p>
        <h1 className="mt-2 text-2xl font-semibold text-foreground">Buy Asset</h1>
      </header>

      {loading ? <p className="text-sm text-foreground/70">Loading connected accounts...</p> : null}
      {error ? <p className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p> : null}
      {message ? <p className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">{message}</p> : null}

      <label className="block text-sm">
        <span className="mb-1 block text-foreground/80">Connected Account</span>
        <select className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={selectedConnectionId} onChange={(event) => setSelectedConnectionId(event.target.value)}>
          <option value="">Select connected account</option>
          {connections.map((item) => (
            <option key={item.exchange_connection_id} value={item.exchange_connection_id}>
              {item.connection_name} - {item.environment}
            </option>
          ))}
        </select>
      </label>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Asset/Product</span>
          <select className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={product} onChange={(event) => setProduct(event.target.value)}>
            {PRODUCT_OPTIONS.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Amount (USD)</span>
          <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={quoteAmount} onChange={(event) => setQuoteAmount(event.target.value)} />
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Paper Account ID</span>
          <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={paperAccountId} onChange={(event) => setPaperAccountId(event.target.value)} placeholder="UUID" />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Live Trading Profile ID</span>
          <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={liveProfileId} onChange={(event) => setLiveProfileId(event.target.value)} placeholder="UUID" />
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Actor</span>
          <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={actor} onChange={(event) => setActor(event.target.value)} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Idempotency Key</span>
          <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} />
        </label>
      </div>

      <div className="rounded-2xl border border-border bg-background/40 p-3 text-sm text-foreground/80">
        <p>Estimated quantity: {estimatedQuantity}</p>
        <p>Estimated fees: {estimatedFees}</p>
      </div>

      <label className="flex items-center gap-2 text-sm text-foreground/85">
        <input type="checkbox" checked={confirmation} onChange={(event) => setConfirmation(event.target.checked)} />
        I explicitly confirm this immediate live BUY.
      </label>

      <button type="button" className="w-full rounded-md border border-cyan-400/40 bg-cyan-500/20 px-4 py-3 text-sm font-semibold text-cyan-50" onClick={() => void onBuyNow()}>
        Buy Now
      </button>

      <div className="rounded-2xl border border-border bg-background/40 p-3 text-sm text-foreground/80">
        <p>State: {status}</p>
        {receipt ? <p>Internal Order ID: {receipt.internal_order_id}</p> : null}
        {receipt?.provider_order_id ? <p>Provider Order ID: {receipt.provider_order_id}</p> : null}
        {receipt ? <p>Requested Amount: {formatCurrency(receipt.requested_amount)}</p> : null}
        {receipt?.executed_quantity ? <p>Executed Quantity: {receipt.executed_quantity}</p> : null}
        {receipt?.average_fill_price ? <p>Average Fill Price: {receipt.average_fill_price}</p> : null}
        {receipt ? <p>Reconciliation State: {receipt.reconciliation_state ?? "pending"}</p> : null}
      </div>

      {showLongPendingMessage ? (
        <p className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
          Submission took longer than 10 seconds. Showing persisted order status and continuing reconciliation polling.
        </p>
      ) : null}

      <button type="button" className="w-full rounded-md border border-emerald-500/40 bg-emerald-500/15 px-4 py-2 text-sm font-semibold text-emerald-100" onClick={() => void onAdopt()} disabled={receipt?.status !== "FILLED"}>
        Adopt Into Autonomous Management (Optional)
      </button>
    </section>
  );
}
