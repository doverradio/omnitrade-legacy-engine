"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import DollarAndPercent from "@/components/domain/DollarAndPercent";
import StartingBalanceInput from "@/components/domain/StartingBalanceInput";
import {
  ApiRequestError,
  createPaperAccount,
  getPaperAccount,
  resetPaperAccount,
  type PaperAccount,
} from "@/lib/api/paperAccounts";

type AccountFormState = {
  name: string;
  assetClass: "crypto" | "stock";
  startingBalance: string;
};

const DEFAULT_FORM_STATE: AccountFormState = {
  name: "Family Paper Account",
  assetClass: "crypto",
  startingBalance: "25",
};

function formatAccountBalance(value: string): string {
  return `$${value}`;
}

function parseDecimal(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export default function PaperTradingPage() {
  const [activeAccount, setActiveAccount] = useState<PaperAccount | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [accountIdInput, setAccountIdInput] = useState("");
  const [formState, setFormState] = useState<AccountFormState>(DEFAULT_FORM_STATE);
  const [loading, setLoading] = useState(true);
  const [loadingMessage, setLoadingMessage] = useState("Loading paper account data...");
  const [pageError, setPageError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [resetError, setResetError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isResetConfirmOpen, setIsResetConfirmOpen] = useState(false);

  const accountLabel = useMemo(() => {
    if (!activeAccount) {
      return "No paper account loaded";
    }

    return activeAccount.name;
  }, [activeAccount]);

  const loadAccount = useCallback(async (accountId?: string) => {
    setLoading(true);
    setPageError(null);
    setLoadingMessage(accountId ? "Loading selected paper account..." : "Loading current paper account...");

    try {
      const account = await getPaperAccount(accountId);
      setActiveAccount(account);
      setSelectedAccountId(account.id);
      setAccountIdInput(account.id);
      setResetError(null);
      return account;
    } catch (error) {
      const message = error instanceof ApiRequestError ? error.message : "Failed to load paper account.";
      setPageError(message);
      setActiveAccount(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAccount();
  }, [loadAccount]);

  const handleCreateAccount = useCallback(async () => {
    const trimmedName = formState.name.trim();
    const startingBalance = Number(formState.startingBalance);

    if (!trimmedName) {
      setFormError("Paper account name is required.");
      return;
    }

    if (!Number.isFinite(startingBalance) || startingBalance < 25) {
      setFormError("Paper Starting Balance must be at least $25.");
      return;
    }

    setIsCreating(true);
    setFormError(null);

    try {
      const createdAccount = await createPaperAccount({
        name: trimmedName,
        asset_class: formState.assetClass,
        starting_balance: formState.startingBalance,
      });

      setActiveAccount(createdAccount);
      setSelectedAccountId(createdAccount.id);
      setAccountIdInput(createdAccount.id);
      setFormState((previous) => ({
        ...previous,
        name: trimmedName,
      }));
      setPageError(null);
      setResetError(null);
    } catch (error) {
      setFormError(error instanceof ApiRequestError ? error.message : "Failed to create paper account.");
    } finally {
      setIsCreating(false);
    }
  }, [formState.assetClass, formState.name, formState.startingBalance]);

  const handleLoadSelectedAccount = useCallback(async () => {
    const accountId = accountIdInput.trim();
    await loadAccount(accountId || undefined);
  }, [accountIdInput, loadAccount]);

  const handleResetAccount = useCallback(async () => {
    if (!activeAccount) {
      setResetError("Load a paper account before attempting a reset.");
      return;
    }

    setIsResetting(true);
    setResetError(null);

    try {
      const resetAccount = await resetPaperAccount({
        account_id: activeAccount.id,
        confirm: true,
      });

      setActiveAccount({
        ...activeAccount,
        current_cash_balance: resetAccount.current_cash_balance,
      });
      setPageError(null);
      setIsResetConfirmOpen(false);
    } catch (error) {
      setResetError(error instanceof ApiRequestError ? error.message : "Failed to reset paper account.");
    } finally {
      setIsResetting(false);
    }
  }, [activeAccount]);

  const displayAssetClass = activeAccount?.asset_class ?? formState.assetClass;
  const equityValue = parseDecimal(activeAccount?.equity);
  const cashValue = parseDecimal(activeAccount?.current_cash_balance);
  const positionValueRollup = Math.max(0, equityValue - cashValue);
  const positions = activeAccount?.positions ?? [];

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border bg-muted/60 p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">Phase 5 shell</p>
        <h1 className="mt-2 text-2xl font-semibold">Portfolio Intelligence + Paper Execution Foundation</h1>
        <p className="mt-2 max-w-3xl text-sm text-foreground/75">
          Paper account lifecycle controls only. This page creates, loads, displays, and resets paper accounts
          using documented endpoints, with explicit PAPER labeling and a $25 minimum proving-ground balance.
        </p>
      </section>

      {pageError ? (
        <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-100">
          Could not load paper account data. {pageError}
        </p>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <section className="rounded-lg border border-border bg-background p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">PAPER ACCOUNT</p>
              <h2 className="mt-1 text-lg font-semibold">Select active paper account</h2>
            </div>
            <button
              type="button"
              onClick={() => void loadAccount()}
              className="rounded-md border border-border bg-muted px-3 py-2 text-sm transition hover:bg-foreground/10"
            >
              Load primary / most recent
            </button>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
            <label className="flex flex-col gap-1 text-sm text-foreground/90">
              <span>Paper account ID</span>
              <input
                value={accountIdInput}
                onChange={(event) => setAccountIdInput(event.target.value)}
                placeholder="Leave blank to load the primary/most recent account"
                className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
              />
            </label>
            <button
              type="button"
              onClick={() => void handleLoadSelectedAccount()}
              className="rounded-md border border-accent bg-accent/20 px-4 py-2 text-sm font-medium transition hover:bg-accent/30"
            >
              Load paper account
            </button>
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <article className="rounded-lg border border-border bg-muted/40 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Loaded account</p>
              <p className="mt-2 text-lg font-semibold">{loading ? loadingMessage : accountLabel}</p>
              <p className="mt-1 text-sm text-foreground/70">
                {loading
                  ? "PAPER data is loading"
                  : activeAccount
                    ? `Selected account ID: ${selectedAccountId}`
                    : "No paper account selected yet"}
              </p>
            </article>

            <article className="rounded-lg border border-border bg-muted/40 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Cash Rollup (PAPER)</p>
              <p className="mt-2 text-lg font-semibold">Paper Cash Balance</p>
              <p className="mt-1 text-sm text-foreground/80">
                {activeAccount ? formatAccountBalance(activeAccount.current_cash_balance) : "$25.00 minimum default"}
              </p>
            </article>

            <article className="rounded-lg border border-border bg-muted/40 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Position Value Rollup (PAPER)</p>
              <p className="mt-2 text-lg font-semibold">Open Position Value</p>
              <p className="mt-1 text-sm text-foreground/80">{`$${positionValueRollup.toFixed(2)}`}</p>
            </article>
          </div>

          <div className="mt-6 rounded-lg border border-border bg-muted/30 p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">
                Paper account metadata
              </h3>
              <span className="rounded-full border border-border bg-background px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
                PAPER
              </span>
            </div>

            {loading ? (
              <div className="mt-4 space-y-3">
                <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/10" />
                <div className="h-4 w-2/3 animate-pulse rounded bg-foreground/10" />
                <div className="h-4 w-1/3 animate-pulse rounded bg-foreground/10" />
              </div>
            ) : activeAccount ? (
              <dl className="mt-4 grid gap-4 sm:grid-cols-2">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Name</dt>
                  <dd className="mt-1 text-sm text-foreground/90">{activeAccount.name}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Account ID</dt>
                  <dd className="mt-1 break-all text-sm text-foreground/90">{activeAccount.id}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Asset class</dt>
                  <dd className="mt-1 text-sm text-foreground/90">{displayAssetClass}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Starting balance</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    Paper Balance: {formatAccountBalance(activeAccount.starting_balance)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Current cash balance</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    Paper Balance: {formatAccountBalance(activeAccount.current_cash_balance)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Paper equity</dt>
                  <dd className="mt-1 text-sm text-foreground/90">Paper Balance: {formatAccountBalance(activeAccount.equity)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Equity return (dollar + percentage)</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    <DollarAndPercent
                      usd={activeAccount.equity_return_usd}
                      pct={activeAccount.equity_return_pct}
                    />
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Status</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    {activeAccount.is_active ? "Active PAPER account" : "Inactive PAPER account"}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="mt-4 text-sm text-foreground/70">
                No paper account loaded. Create one below or load the primary/most recent paper account.
              </p>
            )}
          </div>

          <div className="mt-6 rounded-lg border border-border bg-muted/30 p-4">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">
              Position rollups (PAPER)
            </h3>

            {loading ? (
              <p className="mt-3 text-sm text-foreground/70">Loading position rollups...</p>
            ) : positions.length === 0 ? (
              <p className="mt-3 text-sm text-foreground/70">
                No open PAPER positions. Position value rollups will appear after paper trade activity.
              </p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-foreground/65">
                      <th className="pb-2 pr-4">Symbol</th>
                      <th className="pb-2 pr-4">Quantity</th>
                      <th className="pb-2 pr-4">Avg Entry</th>
                      <th className="pb-2 pr-4">Unrealized P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((position) => (
                      <tr key={position.asset_id} className="border-t border-border/60">
                        <td className="py-2 pr-4 font-medium">{position.symbol}</td>
                        <td className="py-2 pr-4">{position.quantity}</td>
                        <td className="py-2 pr-4">{`$${position.avg_entry_price}`}</td>
                        <td className="py-2 pr-4">
                          <DollarAndPercent usd={position.unrealized_pnl_usd} pct={position.unrealized_pnl_pct} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            <button
              type="button"
              disabled={!activeAccount}
              onClick={() => setIsResetConfirmOpen(true)}
              className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-100 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reset paper account
            </button>
            <button
              type="button"
              onClick={() => void loadAccount(activeAccount?.id)}
              className="rounded-md border border-border bg-muted px-4 py-2 text-sm transition hover:bg-foreground/10"
            >
              Refresh PAPER data
            </button>
          </div>

          {resetError ? (
            <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
              {resetError}
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-border bg-background p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">Create PAPER account</p>
              <h2 className="mt-1 text-lg font-semibold">New paper account</h2>
            </div>
            <span className="rounded-full border border-border bg-muted px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
              $25 minimum
            </span>
          </div>

          <div className="mt-4 space-y-4">
            <label className="flex flex-col gap-1 text-sm text-foreground/90">
              <span>Account name</span>
              <input
                value={formState.name}
                onChange={(event) => setFormState((previous) => ({ ...previous, name: event.target.value }))}
                className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
              />
            </label>

            <label className="flex flex-col gap-1 text-sm text-foreground/90">
              <span>Asset class</span>
              <select
                value={formState.assetClass}
                onChange={(event) =>
                  setFormState((previous) => ({
                    ...previous,
                    assetClass: event.target.value === "stock" ? "stock" : "crypto",
                  }))
                }
                className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
              >
                <option value="crypto">crypto</option>
                <option value="stock">stock</option>
              </select>
            </label>

            <StartingBalanceInput
              id="paper-account-starting-balance"
              label="Paper Account Starting Balance"
              value={formState.startingBalance}
              onChange={(nextValue) => setFormState((previous) => ({ ...previous, startingBalance: nextValue }))}
              min={25}
            />

            <p className="text-xs text-foreground/70">
              The paper account form enforces the documented $25 Small Account Mode floor.
            </p>

            <div className="flex items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => void handleCreateAccount()}
                disabled={isCreating}
                className="rounded-md border border-accent bg-accent/20 px-4 py-2 text-sm font-medium transition hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isCreating ? "Creating..." : "Create paper account"}
              </button>
              <p className="text-xs uppercase tracking-wide text-foreground/60">PAPER ONLY</p>
            </div>

            {formError ? (
              <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
                {formError}
              </p>
            ) : null}
          </div>
        </section>
      </div>

      {isResetConfirmOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-lg">
            <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">Confirm reset</p>
            <h2 className="mt-2 text-lg font-semibold">Reset the active paper account?</h2>
            <p className="mt-2 text-sm text-foreground/75">
              This will reset the selected PAPER account back to its starting balance using the documented
              paper-reset contract. No live account behavior is involved.
            </p>

            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setIsResetConfirmOpen(false)}
                className="rounded-md border border-border bg-muted px-4 py-2 text-sm transition hover:bg-foreground/10"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleResetAccount()}
                disabled={isResetting}
                className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-100 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isResetting ? "Resetting..." : "Confirm reset"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
