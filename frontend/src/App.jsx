import { useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(payload?.detail || payload?.error || "Request failed");
  return payload;
}

export default function App() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("hybrid_rerank");
  const [results, setResults] = useState([]);
  const [status, setStatus] = useState(null);
  const [folder, setFolder] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const resultCount = useMemo(() => results.length, [results]);

  useEffect(() => {
    refreshStatus();
  }, []);

  async function refreshStatus() {
    try {
      setStatus(await request("/index/status"));
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function mountAndIndex(event) {
    event.preventDefault();
    if (!folder.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      const mounted = await request("/sources/folders", {
        method: "POST",
        body: JSON.stringify({ path: folder.trim() }),
      });
      const indexed = await request("/index/run", { method: "POST" });
      setMessage(`Mounted ${mounted.label}: ${indexed.completed} source(s) indexed.`);
      await refreshStatus();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function search(event) {
    event?.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      const payload = await request(`/search?q=${encodeURIComponent(query)}&mode=${mode}`);
      setResults(payload.results || []);
      if (!payload.results?.length) setMessage("No indexed evidence matched that query.");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">R</span><div><strong>Recall</strong><small>your private memory</small></div></div>
        <div className="side-section"><span className="eyebrow">Workspace</span><button className="nav active">⌕ Search</button><button className="nav" disabled>◷ Calendar <em>soon</em></button><button className="nav" disabled>◎ Knowledge map <em>soon</em></button></div>
        <div className="side-section"><span className="eyebrow">Index</span><div className="status-card"><div><span>Sources</span><strong>{status?.total ?? 0}</strong></div><small>{status?.done ?? 0} indexed · {status?.pending ?? 0} pending</small><div className="progress"><i style={{ width: `${status?.total ? (status.done / status.total) * 100 : 0}%` }} /></div></div></div>
        <div className="privacy-note"><span>◉</span><p>Local-first by default.<br />Cloud AI requires explicit configuration.</p></div>
      </aside>
      <section className="content">
        <header className="topbar"><div><span className="eyebrow">Memory search</span><h1>What do you remember?</h1></div><span className="connection">● API connected</span></header>
        <section className="control-panel">
          <form className="mount-form" onSubmit={mountAndIndex}><input value={folder} onChange={(event) => setFolder(event.target.value)} placeholder="Folder path to index…" /><button disabled={busy}>{busy ? "Working…" : "Add folder"}</button></form>
          <form className="search-form" onSubmit={search}><div className="search-input"><span>⌕</span><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by meaning, words, or visual content…" /><kbd>Enter</kbd></div><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="hybrid_rerank">Hybrid + rerank</option><option value="keyword">Keyword</option><option value="semantic">Semantic</option><option value="visual">Visual</option><option value="hybrid">Hybrid</option></select><button className="search-button" disabled={busy}>Search</button></form>
        </section>
        {message && <div className="message">{message}</div>}
        <div className="results-header"><span>{resultCount ? `${resultCount} memories found` : "Search results"}</span><span className="muted">{mode.replace("_", " ")}</span></div>
        <section className="results">{results.map((result) => <ResultCard key={result.unit_id} result={result} />)}{!results.length && <div className="empty"><div className="empty-icon">✦</div><h2>Your memory is waiting</h2><p>Add a folder above, then search your files by idea, phrase, or visual description.</p></div>}</section>
      </section>
    </main>
  );
}

function ResultCard({ result }) {
  return <article className="result-card"><div className="thumbnail">{result.thumbnail_url ? <img src={`${API}${result.thumbnail_url}`} alt="" /> : <span>✦</span>}</div><div className="result-body"><div className="result-meta"><span>{result.source_type}</span>{result.page && <span>Page {result.page}</span>}<span className="match">{result.match_type.join(" + ")}</span></div><h2>{result.title}</h2><p dangerouslySetInnerHTML={{ __html: result.snippet }} /><div className="tags">{result.tags.slice(0, 5).map((tag) => <span key={tag}>#{tag}</span>)}</div></div><a className="open-link" href={`${API}/sources/${result.source_id}/file`} target="_blank" rel="noreferrer">Open ↗</a></article>;
}
