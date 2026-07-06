type ContextualHelpProps = {
  title: string;
  body: string;
};

export function ContextualHelp({ title, body }: ContextualHelpProps) {
  return (
    <details className="mt-2" data-testid={`contextual-help-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>
      <summary className="cursor-pointer rounded-md border border-border/70 bg-muted/20 px-3 py-2 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
        What is this?
      </summary>
      <p className="mt-2 rounded-md border border-border/70 bg-background/20 px-3 py-2 text-sm text-foreground/80">{body}</p>
    </details>
  );
}
