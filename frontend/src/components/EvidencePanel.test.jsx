import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
    expect(screen.getByRole("button", { name: /Reveal in Finder/ })).toBeInTheDocument();
    expect(screen.getByText("Show 1 more")).toBeInTheDocument();
  });

  it("uses the Finder fallback for PPTX sources", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ status: "revealed" }) })));
    const user = userEvent.setup();
    render(<EvidencePanel message={{ citations: [{ reference: 1, source_id: 42, unit_id: 7, title: "Deep Learning", filename: "Unit-1.pptx", source_type: "pptx", excerpt: "Neural networks" }] }} />);

    await user.click(screen.getByRole("button", { name: /Reveal in Finder/ }));
    await waitFor(() => expect(screen.getByText("Revealed in Finder")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith("http://localhost:8000/sources/42/reveal", expect.objectContaining({ method: "POST" }));
  });
});
