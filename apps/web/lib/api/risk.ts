type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export type RiskUsage = {
  used: string;
  limit: string;
  pct_used: string;
};

export type ActiveCooldown = {
  strategy_id: string;
  asset_id: string;
  cooldown_until: string;
  reason: string;
};

export type ActiveNoTradeZone = {
  asset_id: string;
  reason: string;
  since: string;
};

export type RiskStatusResponse = {
  global_kill_switch: {
    engaged: boolean;
    engaged_at: string | null;
    engaged_by: string | null;
    reason: string | null;
  };
  account: {
    account_id: string;
    trading_paused: boolean;
    paused_reason: string | null;
    daily_loss: RiskUsage;
    drawdown: RiskUsage;
    active_cooldowns: ActiveCooldown[];
    active_no_trade_zones: ActiveNoTradeZone[];
  };
};

export type KillSwitchRequest = {
  scope: "global" | "account";
  account_id: string | null;
  reason: string;
  confirm: true;
  actor?: string;
};

export type KillSwitchResponse = {
  scope: "global" | "account";
  account_id: string | null;
  engaged: boolean;
  engaged_at?: string | null;
  engaged_by?: string | null;
  disengaged_at?: string | null;
  disengaged_by?: string | null;
};

export type RiskRules = {
  max_position_size_pct: string;
  max_daily_loss_pct: string;
  max_drawdown_pct: string;
  default_stop_loss_pct: string;
  cooldown_after_losses: number;
  cooldown_duration_hours: number;
};

export type RiskRulesResponse = {
  account_id: string | null;
  rules: RiskRules;
  is_override: boolean;
  system_defaults: RiskRules;
};

export type RiskRulesPatchRequest = {
  account_id: string | null;
  rules: Partial<RiskRules>;
  confirm_loosening?: boolean;
  actor?: string;
};

export class ApiRequestError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    let code: string | undefined;

    try {
      const payload = (await response.json()) as ErrorEnvelope;
      code = payload.error?.code;
      if (payload.error?.message) {
        message = payload.error.message;
      }
    } catch {
      // Keep generic message when the response body is not valid JSON.
    }

    throw new ApiRequestError(message, response.status, code);
  }

  return (await response.json()) as T;
}

export async function getRiskStatus(accountId: string): Promise<RiskStatusResponse> {
  const query = new URLSearchParams();
  query.set("account_id", accountId);
  return requestJson<RiskStatusResponse>(`/risk/status?${query.toString()}`);
}

export async function enableKillSwitch(payload: KillSwitchRequest): Promise<KillSwitchResponse> {
  return requestJson<KillSwitchResponse>("/risk/kill-switch/enable", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function disableKillSwitch(payload: KillSwitchRequest): Promise<KillSwitchResponse> {
  return requestJson<KillSwitchResponse>("/risk/kill-switch/disable", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getRiskRules(accountId: string | null): Promise<RiskRulesResponse> {
  if (!accountId) {
    return requestJson<RiskRulesResponse>("/risk/rules");
  }

  const query = new URLSearchParams();
  query.set("account_id", accountId);
  return requestJson<RiskRulesResponse>(`/risk/rules?${query.toString()}`);
}

export async function patchRiskRules(payload: RiskRulesPatchRequest): Promise<RiskRulesResponse> {
  return requestJson<RiskRulesResponse>("/risk/rules", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}