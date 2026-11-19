import { useEffect, useRef, useState } from "react";
import { API, request, revealSource } from "../api";

const SEARCH_MODES = ["keyword", "semantic", "visual", "hybrid", "hybrid_rerank"];

export default function SearchView({ initialQuery = "" }) {
  const [query, setQuery] = useState(initialQuery);
  const [mode, setMode] = useState("hybrid");
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [searched, setSearched] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    function focusSearch() { inputRef.current?.focus(); }
    window.addEventListener("recall:focus-search", focusSearch);
    return () => window.removeEventListener("recall:focus-search", focusSearch);
  }, []);

  async function search(event) {
    event?.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      const payload = await request(`/search?q=${encodeURIComponent(query.trim())}&mode=${mode}`);
      setResults(payload.results || payload || []);
      setSearched(true);
    } catch (error) {
      setResults([]);
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="search-view">
    <div className="view-intro">
      <span className="eyebrow">Knowledge search</span>
      <h1>Find something you remember.</h1>
      <p>Search your indexed documents, screenshots, images and notes.</p>
    </div>
    <form className="search-form" onSubmit={search}>
      <div className="premium-search">
        <span>⌕</span>
        <input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search files and passages…" autoFocus />
        <button type="submit">{busy ? "Searching…" : "Search"}</button>
      </div>
      <div className="mode-pills">
        {SEARCH_MODES.map((value) => <button type="button" className={mode === value ? "selected" : ""} key={value} onClick={() => setMode(value)}>{value.replace("_", " ")}</button>)}
      </div>
    </form>
    {message && <div className="message error">{message}</div>}
    {results.length > 0 && <div className="search-results">
      <div className="search-results-heading"><span>{results.length} results</span><span>{mode}</span></div>
      {results.map((result, index) => <SearchResult key={result.unit_id || result.source_id || index} result={result} mode={mode} index={index} />)}
    </div>}
    {!busy && !results.length && !message && <div className="memory-empty"><div className="empty-orbit">⌕</div><h3>{searched ? "No matching indexed file was found." : "Search your indexed files"}</h3><p>{searched ? "Try a filename, exact phrase, or a different retrieval mode." : "Find files and relevant passages with keyword, semantic, visual, or hybrid search."}</p></div>}
  </section>;
}

function SearchResult({ result, mode, index }) {
  const [revealMessage, setRevealMessage] = useState("");
  async function reveal() {
    setRevealMessage("");
    try {
      await revealSource(result.source_id);
      setRevealMessage("Opened in Finder");
    } catch (error) {
      setRevealMessage(error.message);
    }
  }

  const content = <>
    <div className="search-result-media">
      {result.thumbnail_url ? <img src={`${API}${result.thumbnail_url}`} alt="" /> : <div className="result-icon">{result.file_type === "pdf" ? "▧" : "✦"}</div>}
    </div>
    <div>
          <div className="memory-card-meta"><span className="event-badge">{Array.isArray(result.match_type) ? result.match_type.join(" · ") : result.match_type || mode}</span>{result.visual_match && <span className="visual-pill">Visual match</span>}</div>
      <h3>{result.title || result.filename || "Untitled result"}</h3>
      <p>{result.snippet || result.text || "Indexed memory"}</p>
      <small>{result.page ? `Page ${result.page} · ` : ""}{result.source_id || ""}</small>
    </div>
  </>;
  const key = result.unit_id || result.source_id || index;
  return result.source_id
    ? <div className="search-result-shell" key={key}><a className="search-result" href={`${API}/sources/${result.source_id}/file`} target="_blank" rel="noreferrer">{content}</a><div className="search-result-actions"><button type="button" onClick={reveal}>Reveal in Finder</button>{revealMessage && <small>{revealMessage}</small>}</div></div>
    : <article className="search-result" key={key}>{content}</article>;
}
