import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConnectionsView from "./ConnectionsView";

const source = { source_id: 10, title: "Synthetic notes", filename: "synthetic.md", source_type: "markdown", file_type: "markdown" };

const entity = (id, title, objectType, aliases = []) => ({
  object_id: id, title, object_type: objectType, aliases, description: `${title} description`,
  created_by: "system", discovery_method: "lexical", status: "active", created_at: "2026-01-01", updated_at: "2026-01-01",
  evidence_count: 1, relationship_count: 1,
});

const detail = {
  entity: entity(1, "Project Aurora", "project", ["Aurora"]),
  evidence: [{ evidence_id: 1, source_id: 10, source_title: "Synthetic notes", file_type: "markdown", unit_id: 7, page: 3, excerpt: "Project Aurora uses a blue indicator.", discovery_method: "lexical", confidence: null, supported: true }],
  relationships: [{ relationship_id: 4, relationship_type: "part_of", is_symmetric: false, from_entity: entity(2, "Research Lab", "organization"), to_entity: entity(1, "Project Aurora", "project"), confidence: 0.8, created_by: "system", discovery_method: "lexical", reviewed_status: "unreviewed", supported: true, explanation: "The lab owns the project.", evidence: [], direction: "incoming", created_at: "2026-01-01", updated_at: "2026-01-01" }],
};

function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

function installApi() {
  const calls = [];
  const fetchMock = vi.fn(async (url, options = {}) => {
    calls.push({ url: String(url), options });
    const parsed = new URL(String(url), "http://localhost");
    const path = parsed.pathname;
    if (path === "/connections/entities" && options.method === undefined) {
      const query = parsed.searchParams.get("q");
      return response({ entities: query ? [entity(1, "Project Aurora", "project", ["Aurora"])] : [entity(1, "Project Aurora", "project", ["Aurora"]), entity(2, "Research Lab", "organization")], total: query ? 1 : 2, limit: 24, offset: 0, has_more: false });
    }
    if (path === "/connections/entities/1") return response(detail);
    if (path === "/connections/sources/10") return response({ source, entities: [{ ...entity(1, "Project Aurora", "project"), evidence: detail.evidence }], relationships: [], entities_total: 1, relationships_total: 0, limit: 50, offset: 0, has_more: false });
    if (path === "/connections/relationships" && options.method === "POST") return response({ ...detail.relationships[0], relationship_id: 9, created_by: "user", discovery_method: "manual", reviewed_status: "confirmed", confidence: null }, 201);
    if (path === "/connections/relationships/4" && options.method === "PATCH") return response({ ...detail.relationships[0], reviewed_status: "confirmed" });
    return response({}, 204);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

describe("ConnectionsView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.confirm = vi.fn(() => true);
    window.open = vi.fn();
  });

  it("renders entities, aliases, filters, and detail evidence from API contracts", async () => {
    const { calls } = installApi();
    const user = userEvent.setup();
    render(<ConnectionsView sources={[source]} />);
    expect(await screen.findByText("Project Aurora")).toBeInTheDocument();
    expect(screen.getByText("Aurora")).toBeInTheDocument();
    expect(await screen.findByText("Research Lab")).toBeInTheDocument();
    expect(await screen.findByText("Project Aurora uses a blue indicator.")).toBeInTheDocument();
    expect(screen.getByText("←")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Search entities" }), "Aurora");
    await waitFor(() => expect(calls.some((call) => call.url.includes("q=Aurora"))).toBe(true));
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by entity type" }), "project");
    await waitFor(() => expect(calls.some((call) => call.url.includes("type=project"))).toBe(true));
  });

  it("loads source connections and opens only the safe source endpoint", async () => {
    installApi();
    const user = userEvent.setup();
    render(<ConnectionsView sources={[source]} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Explore source" }), "10");
    expect(await screen.findByText("Source connections")).toBeInTheDocument();
    expect(screen.getByText("synthetic.md · markdown")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open source" }));
    expect(window.open).toHaveBeenCalledWith(expect.stringContaining("/sources/10/open"), "_blank", "noopener,noreferrer");
  });

  it("creates a manual relationship with backend-owned provenance only", async () => {
    const { calls } = installApi();
    const user = userEvent.setup();
    render(<ConnectionsView sources={[source]} />);
    await user.click(await screen.findByRole("button", { name: "＋ Connect entities" }));
    const modal = screen.getByRole("heading", { name: "Connect two entities" }).closest("form");
    await user.selectOptions(within(modal).getAllByRole("combobox")[0], "1");
    await user.selectOptions(within(modal).getAllByRole("combobox")[1], "related_to");
    await user.selectOptions(within(modal).getAllByRole("combobox")[2], "2");
    await user.click(within(modal).getByRole("button", { name: "Create connection" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/connections/relationships") && call.options.method === "POST")).toBe(true));
    const request = calls.find((call) => call.url.endsWith("/connections/relationships") && call.options.method === "POST");
    expect(JSON.parse(request.options.body)).toEqual({ from_object_id: 1, to_object_id: 2, relationship_type: "related_to" });
  });

  it("exposes review controls for automatic relationships and refreshes status", async () => {
    installApi();
    const user = userEvent.setup();
    render(<ConnectionsView />);
    await screen.findByRole("heading", { name: "Project Aurora" });
    await user.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Relationship confirmed.")).toBeInTheDocument();
  });

  it("shows bounded pagination and empty results without fabricating entities", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      calls.push(String(url));
      const offset = Number(new URL(String(url), "http://localhost").searchParams.get("offset") || 0);
      if (String(url).includes("/connections/entities/")) return response(detail);
      const items = Array.from({ length: offset ? 1 : 24 }, (_, index) => entity(index + offset + 1, `Entity ${index + offset + 1}`, "concept"));
      return response({ entities: items, total: 25, limit: 24, offset, has_more: offset === 0 });
    }));
    const user = userEvent.setup();
    render(<ConnectionsView />);
    expect(await screen.findByText("Entity 1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(calls.some((url) => url.includes("offset=24"))).toBe(true));
    cleanup();
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn(async () => response({ entities: [], total: 0, limit: 24, offset: 0, has_more: false })));
    render(<ConnectionsView />);
    expect(await screen.findByText("No meaningful entities found")).toBeInTheDocument();
  });

  it("renders a safe API error state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: "synthetic request failed" }, 500)));
    render(<ConnectionsView />);
    expect(await screen.findByText("synthetic request failed")).toBeInTheDocument();
  });
});
