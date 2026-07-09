import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ValidationRunTimeline, { type TimelineQuery } from "@/components/domain/ValidationRunTimeline";
import type { ValidationRunEvent } from "@/lib/api/arena";

const DEFAULT_QUERY: TimelineQuery = {
  order: "newest",
  window: "entire_run",
  category: "all",
  severity: "all",
  search: "",
};

function buildEvent(overrides: Partial<ValidationRunEvent>): ValidationRunEvent {
  return {
    id: 1,
    validation_run_id: "11111111-1111-1111-1111-111111111111",
    timestamp: "2026-07-09T00:00:00Z",
    event_type: "VALIDATION_STARTED",
    category: "all",
    severity: "green",
    title: "Validation Started",
    description: "Validation run has started",
    metadata: {},
    ...overrides,
  };
}

describe("ValidationRunTimeline", () => {
  it("renders timeline events", () => {
    render(
      <ValidationRunTimeline
        events={[buildEvent({ id: 1 }), buildEvent({ id: 2, title: "Heartbeat", event_type: "HEARTBEAT", severity: "blue" })]}
        query={DEFAULT_QUERY}
      />,
    );

    expect(screen.getByText("Validation Timeline")).toBeInTheDocument();
    expect(screen.getByText("Validation Started")).toBeInTheDocument();
    expect(screen.getByText("Heartbeat")).toBeInTheDocument();
  });

  it("supports expand and collapse event details", () => {
    render(<ValidationRunTimeline events={[buildEvent({ id: 1 })]} query={DEFAULT_QUERY} />);

    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Event Details")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Event Details")).not.toBeInTheDocument();
  });

  it("renders empty state", () => {
    render(<ValidationRunTimeline events={[]} query={DEFAULT_QUERY} emptyMessage="Timeline is empty" />);

    expect(screen.getByText("Timeline is empty")).toBeInTheDocument();
  });

  it("preserves scroll position when newest events arrive and user is not at top", () => {
    const { rerender } = render(
      <ValidationRunTimeline
        events={[buildEvent({ id: 1 }), buildEvent({ id: 2 })]}
        query={DEFAULT_QUERY}
      />,
    );

    const scroller = screen.getByTestId("validation-run-timeline-scroll");
    Object.defineProperty(scroller, "scrollHeight", { value: 400, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { value: 200, configurable: true });
    Object.defineProperty(scroller, "scrollTop", { value: 80, writable: true, configurable: true });

    rerender(
      <ValidationRunTimeline
        events={[buildEvent({ id: 3 }), buildEvent({ id: 1 }), buildEvent({ id: 2 })]}
        query={DEFAULT_QUERY}
      />,
    );

    Object.defineProperty(scroller, "scrollHeight", { value: 520, configurable: true });
    expect(scroller.scrollTop).toBeGreaterThan(80);
  });

  it("supports filtering controls", () => {
    const onQueryChange = vi.fn();
    render(<ValidationRunTimeline events={[buildEvent({ id: 1 })]} query={DEFAULT_QUERY} onQueryChange={onQueryChange} />);

    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "strategy" } });
    fireEvent.change(screen.getByLabelText("Severity"), { target: { value: "yellow" } });

    expect(onQueryChange).toHaveBeenCalled();
  });
});
