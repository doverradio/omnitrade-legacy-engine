import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ReplayAgentsPanel from "@/components/domain/ReplayAgentsPanel";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ReplayAgentsPanel", () => {
  it("renders the empty state when no replay agents are registered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(200, [])),
    );

    render(<ReplayAgentsPanel />);

    expect(await screen.findByText("Replay Agents")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/No replay agents registered/i)).toBeInTheDocument();
    });
  });

  it("renders the registered placeholder agent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(200, [
          {
            replay_agent_id: "11111111-1111-1111-1111-111111111111",
            name: "Default Replay Agent",
            status: "Registered",
            capabilities: [
              {
                name: "Decision Package consumer",
                description: "Consumes immutable Decision Packages for read-only research analysis.",
              },
            ],
            decision_package_consumer: true,
            execution_logic: false,
            processing_enabled: false,
            scheduling_enabled: false,
            writes_enabled: false,
          },
        ]),
      ),
    );

    render(<ReplayAgentsPanel />);

    expect(await screen.findByText("Default Replay Agent")).toBeInTheDocument();
    expect(screen.getByText("Registered")).toBeInTheDocument();
    expect(screen.getByText(/Decision Package consumer/i)).toBeInTheDocument();
  });
});
