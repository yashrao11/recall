import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SearchView from "./SearchView";

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

describe("SearchView", () => {
  it("keeps source navigation attached to real search results", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      results: [{ unit_id: 7, source_id: 12, title: "Architecture notes", filename: "architecture.pdf", page: 4, snippet: "Residual connections" }],
    })));

    render(<SearchView />);
    fireEvent.change(screen.getByPlaceholderText("Search files and passages…"), { target: { value: "residual" } });
    fireEvent.submit(screen.getByRole("button", { name: "Search" }).closest("form"));

    await waitFor(() => expect(screen.getByRole("link", { name: /Architecture notes/ })).toHaveAttribute(
      "href",
      "http://localhost:8000/sources/12/file",
    ));
  });
});
