import { useRef, useState } from "react";
import { captureFiles, captureNote, captureWebpage, request, retryCapture } from "../api";

const TABS = [
  ["note", "Quick note"],
  ["webpage", "Web link"],
  ["file", "Upload files"],
  ["folder", "Folder"],
];

export default function CaptureMenu({ onClose, onCaptured }) {
  const [tab, setTab] = useState("note");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState("");
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [results, setResults] = useState([]);
  const inputRef = useRef(null);

  function acceptFiles(next) {
    const selected = Array.from(next || []);
    setFiles(selected);
    setMessage(null);
    setResults([]);
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      let payload;
      if (tab === "note") payload = await captureNote({ title: title.trim() || undefined, content });
      else if (tab === "webpage") payload = await captureWebpage({ title: title.trim() || undefined, url: url.trim() });
      else if (tab === "file") {
        if (!files.length) throw new Error("Choose at least one file.");
        payload = await captureFiles(files);
      } else {
        if (!folder.trim()) throw new Error("Enter a folder to import.");
        let mounted;
        try {
          mounted = await request("/sources/folders", { method: "POST", body: JSON.stringify({ path: folder.trim() }) });
        } catch (error) {
          if (error.message !== "folder is already mounted") throw error;
          mounted = await request("/sources/folders/rescan", { method: "POST", body: JSON.stringify({ path: folder.trim() }) });
        }
        const indexing = await request(`/index/run?mount_id=${mounted.mount_id}`, { method: "POST" });
        const inventory = await request(`/sources?mount_id=${mounted.mount_id}`);
        payload = {
          status: indexing.errors ? "captured_with_errors" : "captured",
          items: (inventory.sources || []).map((source) => ({
            source_id: source.source_id,
            title: source.title || source.filename,
            filename: source.filename,
            status: source.excluded
              ? "unavailable"
              : ["done", "partial"].includes(source.indexing_status)
              ? "indexed"
              : source.indexing_status === "error" ? "error" : source.indexing_status,
            indexing_status: source.indexing_status,
            error: source.indexing_error,
          })),
        };
      }
      setResults(payload.items || []);
      const accepted = (payload.items || []).filter((item) => item.status !== "rejected").length;
      const failed = (payload.items || []).filter((item) => ["rejected", "error", "unavailable"].includes(item.status)).length;
      setMessage({ kind: failed ? "warning" : "success", text: `${accepted} item${accepted === 1 ? "" : "s"} received${failed ? ` · ${failed} need attention` : ""}.` });
      onCaptured?.();
      if (tab === "note" || tab === "webpage") {
        setContent(""); setUrl(""); setTitle("");
      }
    } catch (error) {
      setMessage({ kind: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  }

  async function retry(item) {
    setBusy(true);
    try {
      const result = await retryCapture(item.source_id);
      setResults((current) => current.map((entry) => entry.source_id === item.source_id ? result : entry));
      setMessage({ kind: result.status === "error" ? "error" : "success", text: result.status === "error" ? "Retry failed; the source remains available for another retry." : "Indexing retry completed." });
      onCaptured?.();
    } catch (error) {
      setMessage({ kind: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  }

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="content-menu capture-menu" onClick={(event) => event.stopPropagation()}>
      <div className="menu-heading"><div><span className="eyebrow">Universal capture</span><h2>Save to Recall</h2></div><button className="icon-button" onClick={onClose} aria-label="Close capture">×</button></div>
      <div className="capture-tabs" role="tablist" aria-label="Capture type">
        {TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? "selected" : ""} onClick={() => { setTab(value); setMessage(null); }}>{label}</button>)}
      </div>
      <form className="capture-form" onSubmit={submit}>
        {tab === "note" && <>
          <label>Title <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Optional title" maxLength={200} /></label>
          <label>Note <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="Write or paste something worth remembering…" required maxLength={5 * 1024 * 1024} rows={8} /></label>
        </>}
        {tab === "webpage" && <>
          <label>URL <input type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/article" required /></label>
          <label>Title <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Optional title" maxLength={200} /></label>
          <small className="capture-help">Recall retrieves only public HTTP(S) pages and stores the original URL with the captured source.</small>
        </>}
        {tab === "file" && <>
          <div className="capture-dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); acceptFiles(event.dataTransfer.files); }} onClick={() => inputRef.current?.click()}>
            <input ref={inputRef} type="file" hidden multiple aria-label="Choose capture files" accept=".pdf,.pptx,.docx,.txt,.md,.jpg,.jpeg,.png,.webp" onChange={(event) => acceptFiles(event.target.files)} />
            <strong>{files.length ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "Drop files here or choose files"}</strong>
            <small>PDF, PPTX, DOCX, TXT, Markdown, JPG, PNG, WebP · 25 MB each</small>
          </div>
          {files.length > 0 && <div className="capture-file-list">{files.map((file) => <span key={`${file.name}-${file.size}`}>{file.name}</span>)}</div>}
        </>}
        {tab === "folder" && <>
          <label>Mounted folder <input value={folder} onChange={(event) => setFolder(event.target.value)} placeholder="/path/to/folder" required /></label>
          <small className="capture-help">Folder import preserves the existing mounted-folder workflow and indexes supported files.</small>
        </>}
        <button className="primary-button" disabled={busy}>{busy ? "Saving…" : tab === "folder" ? "Mount and index" : "Save to Recall"}</button>
      </form>
      {message && <div className={`capture-message ${message.kind}`}>{message.text}</div>}
      {results.length > 0 && <div className="capture-results"><strong>Processing results</strong>{results.map((item, index) => <div className="capture-result" key={`${item.source_id || item.filename || index}`}><span><b>{item.title || item.filename || "Capture"}</b><small>{item.status === "indexed" ? `Indexed · ${item.indexing_status || "ready"}` : item.status === "duplicate" ? "Already captured" : item.status === "error" ? `Captured, indexing failed${item.error ? ` · ${item.error}` : ""}` : item.status === "unavailable" ? `File unavailable${item.error ? ` · ${item.error}` : ""}` : item.status === "pending" ? "Waiting for indexing" : item.status === "processing" ? "Indexing…" : item.error || "Rejected"}</small></span>{item.status === "error" && item.source_id && <button onClick={() => retry(item)} disabled={busy}>Retry</button>}</div>)}</div>}
    </div>
  </div>;
}
