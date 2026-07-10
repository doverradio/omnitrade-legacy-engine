import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useStablePolling } from "@/lib/useStablePolling";

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function flushUpdates(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

function PollHarness({ fetcher, intervalMs = 1000 }: { fetcher: (signal: AbortSignal) => Promise<number>; intervalMs?: number }) {
  const polling = useStablePolling(fetcher, { intervalMs, enabled: true });

  return (
    <div>
      <p>data:{polling.data == null ? "none" : polling.data}</p>
      <p>initial:{polling.initialLoading ? "yes" : "no"}</p>
      <p>refreshing:{polling.refreshing ? "yes" : "no"}</p>
      <button type="button" onClick={() => void polling.refreshNow()}>
        refresh
      </button>
    </div>
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useStablePolling", () => {
  it("keeps previous data visible while a background refresh is in flight", async () => {
    vi.useFakeTimers();

    const first = createDeferred<number>();
    const second = createDeferred<number>();
    let callCount = 0;
    const fetcher = vi.fn<(_: AbortSignal) => Promise<number>>(() => {
      callCount += 1;
      if (callCount === 1) {
        return first.promise;
      }
      return second.promise;
    });

    render(<PollHarness fetcher={fetcher} intervalMs={1000} />);

    await flushUpdates();

    expect(screen.getByText("data:none")).toBeInTheDocument();
    expect(screen.getByText("initial:yes")).toBeInTheDocument();

    first.resolve(1);
    await first.promise;
    await flushUpdates();

    expect(screen.getByText("data:1")).toBeInTheDocument();
    expect(screen.getByText("initial:no")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.getByText("data:1")).toBeInTheDocument();
    expect(screen.getByText("refreshing:yes")).toBeInTheDocument();

    second.resolve(2);
    await second.promise;
    await flushUpdates();

    expect(screen.getByText("data:2")).toBeInTheDocument();
    expect(screen.getByText("refreshing:no")).toBeInTheDocument();
  });

  it("prevents overlapping refresh calls while a request is already in flight", async () => {
    vi.useFakeTimers();

    const first = createDeferred<number>();
    const second = createDeferred<number>();
    let callCount = 0;
    const fetcher = vi.fn<(_: AbortSignal) => Promise<number>>(() => {
      callCount += 1;
      if (callCount === 1) {
        return first.promise;
      }
      return second.promise;
    });

    render(<PollHarness fetcher={fetcher} intervalMs={60000} />);

    await flushUpdates();
    first.resolve(1);
    await first.promise;
    await flushUpdates();

    expect(screen.getByText("data:1")).toBeInTheDocument();
    expect(screen.getByText("refreshing:no")).toBeInTheDocument();

    const refreshButton = screen.getByRole("button", { name: "refresh" });
    const callsBeforeManualRefresh = fetcher.mock.calls.length;

    await act(async () => {
      fireEvent.click(refreshButton);
      fireEvent.click(refreshButton);
    });

    expect(fetcher.mock.calls.length).toBe(callsBeforeManualRefresh + 1);
    expect(screen.getByText("refreshing:yes")).toBeInTheDocument();

    second.resolve(3);
    await second.promise;
    await flushUpdates();

    expect(screen.getByText("data:3")).toBeInTheDocument();
    expect(screen.getByText("refreshing:no")).toBeInTheDocument();
  });
});
