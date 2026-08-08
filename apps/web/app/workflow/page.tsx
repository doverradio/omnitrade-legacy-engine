"use client";

import { useState } from "react";

type TabId = "input" | "process" | "output";

const TABS: Array<{ id: TabId; label: string }> = [
  { id: "input", label: "Input" },
  { id: "process", label: "Process" },
  { id: "output", label: "Output" },
];

const PROCESS_STAGES = [
  "Observe Market",
  "Determine Market State",
  "Determine Opportunity",
  "Construct Trade",
  "Authorize Trade",
  "Execute",
  "Monitor",
  "Exit",
  "Return Capital",
];

export default function WorkflowPage() {
  const [activeTab, setActiveTab] = useState<TabId>("input");

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="text-lg font-semibold text-foreground">Trade Workflow</h1>
        <p className="mt-1 text-sm text-foreground/60">Capital moves from input, through process, to output.</p>
      </header>

      <div role="tablist" aria-label="Trade workflow" className="flex gap-2 border-b border-border">
        {TABS.map((tab) => {
          const active = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`workflow-tab-${tab.id}`}
              aria-selected={active}
              aria-controls={`workflow-panel-${tab.id}`}
              className={`border-b-2 px-3 py-2 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                active ? "border-accent text-foreground" : "border-transparent text-foreground/60 hover:text-foreground/85"
              }`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id="workflow-panel-input" aria-labelledby="workflow-tab-input" hidden={activeTab !== "input"}>
        {activeTab === "input" ? <InputPanel /> : null}
      </div>

      <div role="tabpanel" id="workflow-panel-process" aria-labelledby="workflow-tab-process" hidden={activeTab !== "process"}>
        {activeTab === "process" ? <ProcessPanel /> : null}
      </div>

      <div role="tabpanel" id="workflow-panel-output" aria-labelledby="workflow-tab-output" hidden={activeTab !== "output"}>
        {activeTab === "output" ? <OutputPanel /> : null}
      </div>
    </div>
  );
}

function InputPanel() {
  return (
    <section className="rounded-lg border border-border bg-muted/30 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Capital</h2>
      <p className="mt-3 text-sm text-foreground/55">Future input controls will go here.</p>
    </section>
  );
}

function ProcessPanel() {
  return (
    <ol className="space-y-3">
      {PROCESS_STAGES.map((stage, index) => (
        <li key={stage} className="flex items-start gap-3">
          <span
            className="mt-3 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-background/60 text-xs font-semibold text-foreground/75"
            aria-hidden="true"
          >
            {index + 1}
          </span>
          <details className="w-full rounded-lg border border-border bg-muted/30">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-foreground/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              {stage}
            </summary>
            <p className="border-t border-border px-4 py-3 text-sm text-foreground/55">Not configured yet.</p>
          </details>
        </li>
      ))}
    </ol>
  );
}

function OutputPanel() {
  return (
    <section className="rounded-lg border border-border bg-muted/30 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Capital Increased</h2>
      <p className="mt-3 text-sm text-foreground/55">Future output details will go here.</p>
    </section>
  );
}
