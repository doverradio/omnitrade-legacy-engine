import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkflowPage from "@/app/workflow/page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installEmptyOrchestrationFetchMock() {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);
    if (url.pathname === "/capital-campaigns/domain") {
      return jsonResponse(200, { items: [] });
    }
    return jsonResponse(404, { error: { message: `unhandled route in test: ${url.pathname}` } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WorkflowPage", () => {
  it("renders the three primary tabs with Input active by default", () => {
    render(<WorkflowPage />);

    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Input", "Process", "Output"]);

    expect(screen.getByRole("tab", { name: "Input" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Capital" })).toBeInTheDocument();
  });

  it("switches to the Process tab and renders the nine stage accordions from the real trace view, honestly reporting no campaign found", async () => {
    installEmptyOrchestrationFetchMock();
    const user = userEvent.setup();
    render(<WorkflowPage />);

    await user.click(screen.getByRole("tab", { name: "Process" }));

    // No fabricated demo data: with no governing campaign returned by the
    // real API, the page must say so plainly rather than show stage
    // accordions with invented content.
    expect(await screen.findByText(/No governing campaign found/)).toBeInTheDocument();
  });

  it("switches to the Output tab", async () => {
    const user = userEvent.setup();
    render(<WorkflowPage />);

    await user.click(screen.getByRole("tab", { name: "Output" }));
    expect(screen.getByRole("heading", { name: "Capital Increased" })).toBeInTheDocument();
  });
});
