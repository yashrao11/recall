import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import StudyView from "./StudyView";

function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

describe("StudyView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("offers partial sources and reports local cloud-disabled generation", async () => {
    const fetchMock = vi.fn(async (url) => {
      const path = new URL(String(url), "http://localhost").pathname;
      if (path === "/study/due") return response({ cards: [], total: 0, limit: 30 });
      if (path === "/connections/entities") return response({ entities: [{ object_id: 8, title: "Neural networks", object_type: "topic" }] });
      if (path === "/study/generate") return response({ status: "ready", message: "Cloud AI is disabled. Recall created an extractive study set locally from the indexed evidence.", title: "Notes", cards: [{ card_id: 1, card_type: "flashcard", front: "Complete the statement", back: "indexed" }], evidence: [] });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<StudyView sources={[{ source_id: 3, title: "Notes", indexing_status: "partial" }]} />);
    expect(await screen.findByRole("option", { name: /Neural networks/ })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Source"), "3");
    await user.click(screen.getByRole("button", { name: "Generate study set" }));
    expect(await screen.findByText(/Cloud AI is disabled/)).toBeInTheDocument();
    expect(screen.getByText("Complete the statement")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/study/generate"), expect.objectContaining({ method: "POST" })));
  });

  it("renders grounded cards and sends SM-2 responses", async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      const path = new URL(String(url), "http://localhost").pathname;
      if (path === "/study/due") return response({ cards: [], total: 0, limit: 30 });
      if (path === "/connections/entities") return response({ entities: [] });
      if (path === "/study/generate") return response({ status: "ready", title: "Notes", evidence: [{ source_id: 3, filename: "notes.pdf", page: 2 }], cards: [{ card_id: 11, card_type: "flashcard", front: "What is Recall?", back: "A memory workspace", evidence: [{ source_id: 3, filename: "notes.pdf", page: 2 }] }] });
      if (path === "/study/respond") return response({ card_id: 11, quality: 5, interval_days: 6, ease_factor: 2.6 });
      if (path === "/study/sessions") return response({ session_id: 4, event_id: 9 });
      return response({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<StudyView sources={[{ source_id: 3, title: "Notes", indexing_status: "done" }]} />);
    await user.selectOptions(await screen.findByLabelText("Source"), "3");
    await user.click(screen.getByRole("button", { name: "Generate study set" }));
    expect(await screen.findByText("What is Recall?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review all →" }));
    await user.click(screen.getByRole("button", { name: "Show answer" }));
    await user.click(screen.getByRole("button", { name: "Perfect" }));
    expect(await screen.findByText("Session complete")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/study/respond"))).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/study/sessions"))).toBe(true);
  });
});
