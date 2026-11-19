import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EvidencePanel from "./EvidencePanel";

describe("EvidencePanel", () => {
  it("groups repeated passages from one PPTX source and labels it correctly", () => {
    render(<EvidencePanel message={{ citations: [
      { reference: 2, source_id: 42, unit_id: 7, title: "Deep Learning", filename: "Unit-1.pptx", source_type: "pptx", page: 31, excerpt: "Neural networks", source_url: "/sources/42/open" },
      { reference: 4, source_id: 42, unit_id: 8, title: "Deep Learning", filename: "Unit-1.pptx", source_type: "pptx", page: 32, excerpt: "Gradient flow", source_url: "/sources/42/open" },
    ] }} />);

    expect(screen.getByText("PPT")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Deep Learning" })).toBeInTheDocument();
    expect(screen.getByText("[2, 4] · pptx")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open source/ })).toHaveAttribute(
      "href",
      "http://localhost:8000/sources/42/open",
    );
    expect(screen.getByText("Show 1 more")).toBeInTheDocument();
  });
});
