import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import WorkflowPage from "@/app/workflow/page";

describe("WorkflowPage", () => {
  it("renders the three primary tabs with Input active by default", () => {
    render(<WorkflowPage />);

    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Input", "Process", "Output"]);

    expect(screen.getByRole("tab", { name: "Input" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Capital" })).toBeInTheDocument();
  });

  it("switches to the Process tab and lists all nine stages as collapsed accordions", async () => {
    const user = userEvent.setup();
    render(<WorkflowPage />);

    await user.click(screen.getByRole("tab", { name: "Process" }));

    const stages = [
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

    for (const stage of stages) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }

    const detailsElements = document.querySelectorAll("details");
    expect(detailsElements).toHaveLength(9);
    detailsElements.forEach((details) => expect(details.open).toBe(false));

    await user.click(screen.getByText("Observe Market"));
    expect(detailsElements[0].open).toBe(true);
  });

  it("switches to the Output tab", async () => {
    const user = userEvent.setup();
    render(<WorkflowPage />);

    await user.click(screen.getByRole("tab", { name: "Output" }));
    expect(screen.getByRole("heading", { name: "Capital Increased" })).toBeInTheDocument();
  });
});
