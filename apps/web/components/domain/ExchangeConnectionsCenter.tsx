"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/arena";
import {
  getExchangeConnections,
  refreshExchangeAccount,
  refreshExchangeBalances,
  refreshExchangePermissions,
  saveExchangeConnection,
  testExchangeConnection,
  type ExchangeConnection,
  type ExchangeConnectionStatus,
  type ExchangeEnvironment,
} from "@/lib/api/exchange-connections";

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load exchange connections.";
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

function formatValue(value: string | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  const asNumber = Number(value);
  if (Number.isNaN(asNumber)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(asNumber);
}

function statusBadgeClass(status: ExchangeConnectionStatus): string {
  if (status === "connected") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "error") {
    return "border-rose-500/40 bg-rose-500/10 text-rose-100";
  }
  return "border-amber-500/40 bg-amber-500/10 text-amber-100";
}

export default function ExchangeConnectionsCenter() {
  const [items, setItems] = useState<ExchangeConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);

  const [connectionName, setConnectionName] = useState("Primary Coinbase");
  const [environment, setEnvironment] = useState<ExchangeEnvironment>("sandbox");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [passphrase, setPassphrase] = useState("");

  async function loadConnections() {
    const payload = await getExchangeConnections();
    setItems(payload.items);
  }

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const payload = await getExchangeConnections();
        if (!active) {
          return;
        }
        setItems(payload.items);
      } catch (requestError) {
        if (active) {
          setError(errorMessage(requestError));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, []);

  const coinbaseConnection = useMemo(
    () => items.find((item) => item.provider === "coinbase_advanced") ?? null,
    [items],
  );

  async function handleTestConnection() {
    setSubmitting(true);
    setActionMessage(null);
    try {
      const result = await testExchangeConnection({
        provider: "coinbase_advanced",
        environment,
        api_key: apiKey,
        api_secret: apiSecret,
        passphrase,
      });

      if (result.authenticated) {
        setActionMessage("Connection test succeeded.");
      } else {
        setActionMessage(result.error ?? "Connection test failed.");
      }
    } catch (requestError) {
      setActionMessage(errorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSave() {
    setSubmitting(true);
    setActionMessage(null);
    try {
      await saveExchangeConnection({
        provider: "coinbase_advanced",
        connection_name: connectionName,
        environment,
        api_key: apiKey,
        api_secret: apiSecret,
        passphrase,
      });
      setApiKey("");
      setApiSecret("");
      setPassphrase("");
      setActionMessage("Connection saved.");
      await loadConnections();
    } catch (requestError) {
      setActionMessage(errorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  async function refreshOne(connectionId: string, action: "balances" | "account" | "permissions") {
    setRefreshingId(connectionId + action);
    setActionMessage(null);
    try {
      if (action === "balances") {
        await refreshExchangeBalances(connectionId);
      } else if (action === "account") {
        await refreshExchangeAccount(connectionId);
      } else {
        await refreshExchangePermissions(connectionId);
      }
      await loadConnections();
      setActionMessage("Refresh completed.");
    } catch (requestError) {
      setActionMessage(errorMessage(requestError));
    } finally {
      setRefreshingId(null);
    }
  }

  return (
    <div className="space-y-6 overflow-x-hidden">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Exchange Connections</h1>
        <p className="max-w-4xl text-sm text-foreground/75">
          Read-only secure exchange connectivity for account introspection and readiness checks. No live orders are placed in v1.
        </p>
      </header>

      {error ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          {error}
        </section>
      ) : null}

      {actionMessage ? (
        <section className="rounded-2xl border border-border bg-background/50 p-3 text-sm text-foreground/85">{actionMessage}</section>
      ) : null}

      <section className="rounded-2xl border border-border/80 bg-slate-950/40 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Add Exchange</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Connection Name</span>
            <input className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={connectionName} onChange={(event) => setConnectionName(event.target.value)} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Environment</span>
            <select className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={environment} onChange={(event) => setEnvironment(event.target.value as ExchangeEnvironment)}>
              <option value="sandbox">Sandbox</option>
              <option value="production">Production</option>
            </select>
          </label>
          <label className="text-sm md:col-span-2">
            <span className="mb-1 block text-foreground/80">API Key</span>
            <input type="password" className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
          </label>
          <label className="text-sm md:col-span-2">
            <span className="mb-1 block text-foreground/80">API Secret</span>
            <input type="password" className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={apiSecret} onChange={(event) => setApiSecret(event.target.value)} />
          </label>
          <label className="text-sm md:col-span-2">
            <span className="mb-1 block text-foreground/80">Passphrase (if required)</span>
            <input type="password" className="w-full rounded-md border border-border bg-background/60 px-3 py-2" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className="rounded-md border border-cyan-400/40 bg-cyan-500/20 px-3 py-2 text-sm font-semibold text-cyan-50" onClick={() => void handleTestConnection()} disabled={submitting || !apiKey || !apiSecret}>
            Test Connection
          </button>
          <button type="button" className="rounded-md border border-emerald-400/40 bg-emerald-500/20 px-3 py-2 text-sm font-semibold text-emerald-50" onClick={() => void handleSave()} disabled={submitting || !connectionName || !apiKey || !apiSecret}>
            Save
          </button>
        </div>
      </section>

      {loading ? <section className="rounded-2xl border border-border bg-muted/30 p-3 text-sm text-foreground/80">Loading exchange connections...</section> : null}

      {coinbaseConnection ? (
        <section className="rounded-2xl border border-border/80 bg-slate-950/40 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-foreground">{coinbaseConnection.provider_label}</h2>
              <p className="text-sm text-foreground/70">{coinbaseConnection.connection_name}</p>
            </div>
            <span className={`rounded-full border px-3 py-1 text-xs font-medium uppercase tracking-wide ${statusBadgeClass(coinbaseConnection.status)}`}>
              {coinbaseConnection.status}
            </span>
          </div>

          <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2 xl:grid-cols-3">
            <p>Environment: {coinbaseConnection.environment}</p>
            <p>Last successful sync: {formatTimestamp(coinbaseConnection.last_successful_sync_at)}</p>
            <p>Last heartbeat: {formatTimestamp(coinbaseConnection.last_heartbeat_at)}</p>
            <p>Account status: {coinbaseConnection.account_status ?? "Not available"}</p>
            <p>API permissions: {coinbaseConnection.api_permissions.length ? coinbaseConnection.api_permissions.join(", ") : "Not available"}</p>
            <p>Last API error: {coinbaseConnection.last_api_error ?? "None"}</p>
          </div>

          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <article className="rounded-xl border border-border bg-background/50 p-3 text-sm">
              <p className="text-[11px] uppercase tracking-wide text-foreground/65">API Key</p>
              <p className="mt-1 text-foreground">{coinbaseConnection.credential_mask.api_key}</p>
            </article>
            <article className="rounded-xl border border-border bg-background/50 p-3 text-sm">
              <p className="text-[11px] uppercase tracking-wide text-foreground/65">API Secret</p>
              <p className="mt-1 text-foreground">{coinbaseConnection.credential_mask.api_secret}</p>
            </article>
            <article className="rounded-xl border border-border bg-background/50 p-3 text-sm">
              <p className="text-[11px] uppercase tracking-wide text-foreground/65">Passphrase</p>
              <p className="mt-1 text-foreground">{coinbaseConnection.credential_mask.passphrase ?? "Not configured"}</p>
            </article>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-md border border-cyan-400/40 bg-cyan-500/20 px-3 py-2 text-xs font-semibold text-cyan-50"
              onClick={() => void refreshOne(coinbaseConnection.exchange_connection_id, "balances")}
              disabled={refreshingId != null}
            >
              Refresh Balances
            </button>
            <button
              type="button"
              className="rounded-md border border-cyan-400/40 bg-cyan-500/20 px-3 py-2 text-xs font-semibold text-cyan-50"
              onClick={() => void refreshOne(coinbaseConnection.exchange_connection_id, "account")}
              disabled={refreshingId != null}
            >
              Refresh Account
            </button>
            <button
              type="button"
              className="rounded-md border border-cyan-400/40 bg-cyan-500/20 px-3 py-2 text-xs font-semibold text-cyan-50"
              onClick={() => void refreshOne(coinbaseConnection.exchange_connection_id, "permissions")}
              disabled={refreshingId != null}
            >
              Refresh Permissions
            </button>
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            <section className="rounded-xl border border-border bg-background/50 p-3">
              <h3 className="text-sm font-semibold text-foreground">Balances</h3>
              <div className="mt-2 overflow-x-auto">
                <table className="min-w-[460px] w-full text-left text-sm">
                  <thead className="text-foreground/70">
                    <tr>
                      <th className="px-2 py-1">Currency</th>
                      <th className="px-2 py-1">Available</th>
                      <th className="px-2 py-1">Reserved</th>
                      <th className="px-2 py-1">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {coinbaseConnection.balances.map((balance) => (
                      <tr key={balance.currency} className="border-t border-border">
                        <td className="px-2 py-1">{balance.currency}</td>
                        <td className="px-2 py-1">{formatValue(balance.available)}</td>
                        <td className="px-2 py-1">{formatValue(balance.reserved)}</td>
                        <td className="px-2 py-1">{formatValue(balance.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-sm text-foreground/75">Total Equity (USD): {formatValue(coinbaseConnection.total_equity_usd)}</p>
            </section>

            <section className="rounded-xl border border-border bg-background/50 p-3">
              <h3 className="text-sm font-semibold text-foreground">Live Readiness</h3>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {coinbaseConnection.readiness_checks.map((item) => (
                  <article key={item.code} className={`rounded-lg border p-2 text-sm ${item.ok ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100" : "border-amber-500/40 bg-amber-500/10 text-amber-100"}`}>
                    <p className="font-medium">{item.label}</p>
                    <p className="text-xs opacity-90">{item.detail}</p>
                  </article>
                ))}
              </div>
            </section>
          </div>
        </section>
      ) : null}
    </div>
  );
}
