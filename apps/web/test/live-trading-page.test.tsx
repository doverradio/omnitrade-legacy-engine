import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LiveTradingPage from "@/app/live-trading/page";

const mockGetLiveRegistrationStatus = vi.fn();
const mockGetLiveApprovalsStatus = vi.fn();
const mockGetLiveReconciliationStatus = vi.fn();
const mockGetLiveExecutionQuality = vi.fn();
const mockGetLiveComplianceEvidence = vi.fn();
const mockExportLiveComplianceBundle = vi.fn();

vi.mock("@/lib/api/live", () => ({
  getLiveRegistrationStatus: (...args: unknown[]) => mockGetLiveRegistrationStatus(...args),
  getLiveApprovalsStatus: (...args: unknown[]) => mockGetLiveApprovalsStatus(...args),
  getLiveReconciliationStatus: (...args: unknown[]) => mockGetLiveReconciliationStatus(...args),
  getLiveExecutionQuality: (...args: unknown[]) => mockGetLiveExecutionQuality(...args),
  getLiveComplianceEvidence: (...args: unknown[]) => mockGetLiveComplianceEvidence(...args),
  exportLiveComplianceBundle: (...args: unknown[]) => mockExportLiveComplianceBundle(...args),
  ApiRequestError: class ApiRequestError extends Error {
    status: number;

    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

describe("LiveTradingPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockGetLiveRegistrationStatus.mockResolvedValue({
      status_state: "available",
      readiness_state: "enabled",
      operating_mode: "live",
      approval_state: "approved",
      risk_authority_model: "risk_engine_final",
      warnings: [{ code: "operator_controlled_live_mode", message: "Live trading is controlled" }],
    });
    mockGetLiveApprovalsStatus.mockResolvedValue({
      status_state: "available",
      total_events: 1,
      items: [{ checkpoint_type: "first_live_enablement", approval_state: "approved" }],
      warnings: [],
    });
    mockGetLiveReconciliationStatus.mockResolvedValue({
      status_state: "unavailable",
      unresolved_count: 0,
      latest_reconciliation_status: null,
      latest_recorded_at: null,
      warnings: [{ code: "paper_mode_active", message: "Paper remains default" }],
    });
    mockGetLiveExecutionQuality.mockResolvedValue({
      status_state: "unavailable",
      total_records: 0,
      average_slippage_bps: null,
      unknown_or_unavailable_records: 0,
      warnings: [],
    });
    mockGetLiveComplianceEvidence.mockResolvedValue({
      status_state: "unavailable",
      total_records: 0,
      items: [],
      warnings: [],
    });
    mockExportLiveComplianceBundle.mockResolvedValue({
      status_state: "available",
      exported_by: "operator:compliance",
      exported_at: "2026-07-06T12:00:00Z",
      total_records: 0,
      warnings: [{ code: "read_only_export", message: "Read only" }],
    });
  });

  it("loads live operational read surfaces and shows fail-visible state labels", async () => {
    const user = userEvent.setup();
    render(<LiveTradingPage />);

    await user.type(screen.getByPlaceholderText("Enter live_trading_profile_id"), "profile-1");
    await user.click(screen.getByRole("button", { name: "Load Operational Status" }));

    expect(await screen.findByText("Registration / Status")).toBeInTheDocument();
    const unavailableLabels = screen.getAllByText(/Unavailable \(fail visible\)/);
    expect(unavailableLabels.length).toBeGreaterThan(0);
    expect(screen.getByText(/operator_controlled_live_mode/)).toBeInTheDocument();
  });
});
