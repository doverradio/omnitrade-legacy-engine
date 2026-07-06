import { useId } from "react";

type GlossaryTermTooltipProps = {
  term: string;
  definition: string;
};

export function GlossaryTermTooltip({ term, definition }: GlossaryTermTooltipProps) {
  const definitionId = useId();

  return (
    <span className="inline-flex items-center gap-1">
      <span>{term}</span>
      <button
        type="button"
        aria-label={`${term} definition`}
        aria-describedby={definitionId}
        title={definition}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-border bg-background/60 text-[10px] font-semibold text-foreground/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        ?
      </button>
      <span id={definitionId} className="sr-only">
        {definition}
      </span>
    </span>
  );
}
