"use client";

import { useEffect, useState } from "react";

import { ApiRequestError, getReplayAgents, type ReplayAgentRegistration } from "@/lib/api/arena";

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load replay agents.";
}

export default function ReplayAgentsPanel() {
  const [items, setItems] = useState<ReplayAgentRegistration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const payload = await getReplayAgents();
        if (active) {
          setItems(Array.isArray(payload) ? payload : []);
        }
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

  return (
    <article className="rounded-xl border border-dashed border-border bg-background/40 p-4">
      <h3 className="text-base font-semibold">Replay Agents</h3>
      <p className="mt-1 text-sm text-foreground/70">
        Replay agents analyze immutable Decision Packages without affecting production. They are read-only research components.
      </p>

      {error ? <p className="mt-3 text-sm text-rose-200">{error}</p> : null}

      {loading ? (
        <p className="mt-3 text-sm text-foreground/70">Loading replay agents...</p>
      ) : items.length === 0 ? (
        <div className="mt-3 rounded-lg border border-border bg-background/50 p-4 text-sm text-foreground/70">
          <p className="font-medium text-foreground/90">No replay agents registered.</p>
          <p className="mt-1">Replay agents will analyze immutable Decision Packages without affecting production.</p>
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          {items.map((item) => (
            <div key={item.replay_agent_id} className="rounded-lg border border-border bg-background/50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-semibold text-foreground/90">{item.name}</p>
                  <p className="text-xs text-foreground/60">{item.replay_agent_id}</p>
                </div>
                <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-xs font-medium text-emerald-100">
                  {item.status}
                </span>
              </div>

              <p className="mt-3 text-sm text-foreground/75">Capabilities</p>
              <ul className="mt-2 space-y-1 text-sm text-foreground/80">
                {item.capabilities.map((capability) => (
                  <li key={capability.name} className="rounded-md border border-border/70 bg-background/60 px-3 py-2">
                    <p className="font-medium text-foreground/90">{capability.name}</p>
                    <p className="text-xs text-foreground/65">{capability.description}</p>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
