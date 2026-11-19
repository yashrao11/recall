import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CaptureMenu from "./CaptureMenu";

function response(payload, status = 201) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

describe("CaptureMenu", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("captures a quick note and reports indexing status", async () => {
    const fetchMock = vi.fn(async () => response({ status: "captured", items: [{ status: "indexed", source_id: 4, title: "Meeting note", indexing_status: "partial" }] }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onCaptured = vi.fn();
    render(<CaptureMenu onClose={vi.fn()} onCaptured={onCaptured} />);
    await user.type(screen.getByLabelText("Title"), "Meeting note");
    await user.type(screen.getByLabelText("Note"), "Remember the retrieval design.");
    await user.click(screen.getByRole("button", { name: "Save to Recall" }));
    expect(await screen.findByText(/1 item received/)).toBeInTheDocument();
    expect(screen.getByText(/Ready · partial/)).toBeInTheDocument();
    expect(onCaptured).toHaveBeenCalled();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ type: "note", title: "Meeting note" });
  });

  it("uploads multiple files with multipart form data", async () => {
    const fetchMock = vi.fn(async () => response({ status: "captured", items: [{ status: "indexed", source_id: 5, title: "notes.txt" }] }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CaptureMenu onClose={vi.fn()} onCaptured={vi.fn()} />);
    await user.click(screen.getByRole("tab", { name: "Upload files" }));
    const input = screen.getByLabelText("Choose capture files");
    const file = new File(["captured text"], "notes.txt", { type: "text/plain" });
    await user.upload(input, file);
    await user.click(screen.getByRole("button", { name: "Save to Recall" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const request = fetchMock.mock.calls[0][1];
    expect(request.body).toBeInstanceOf(FormData);
    expect(request.headers).toEqual({});
    expect((await screen.findAllByText("notes.txt")).length).toBeGreaterThanOrEqual(1);
  });

  it("shows backend validation errors without closing the capture flow", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: "unsafe URL" }, 400)));
    const user = userEvent.setup();
    render(<CaptureMenu onClose={vi.fn()} onCaptured={vi.fn()} />);
    await user.click(screen.getByRole("tab", { name: "Web link" }));
    await user.type(screen.getByLabelText("URL"), "http://127.0.0.1/private");
    await user.click(screen.getByRole("button", { name: "Save to Recall" }));
    expect(await screen.findByText("unsafe URL")).toBeInTheDocument();
  });
});
