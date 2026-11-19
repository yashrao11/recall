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
  const [expanded, setExpanded] = useState(false);
  async function reveal() {
    setRevealMessage("");
    try {
      await revealSource(result.source_id);
      setRevealMessage("Opened in Finder");
    } catch (error) {
      setRevealMessage(error.message);
    }
  }

  const passages = result.passages || [];
  const visiblePassages = expanded ? passages.slice(1) : [];
  const excerpt = readableSnippet(result.snippet || result.text || "", result.source_type);
  const matchTypes = Array.isArray(result.match_type) ? result.match_type : [result.match_type || mode];
  const content = <>
    <div className="search-result-media">
      {result.thumbnail_url ? <img src={`${API}${result.thumbnail_url}`} alt="" /> : <div className="result-icon">{fileIcon(result.filename, result.source_type)}</div>}
    </div>
    <div>
      <div className="memory-card-meta"><span className="event-badge">{matchTypes[0]}</span>{matchTypes.length > 1 && <span className="result-match-count">+{matchTypes.length - 1} match modes</span>}</div>
      <h3>{result.title || result.filename || "Untitled result"}</h3>
      <p>{excerpt}</p>
      <small>{result.page ? `Page ${result.page} · ` : ""}{result.source_type || "source"}{result.additional_passages ? ` · ${result.additional_passages} more passages` : ""}</small>
    </div>
  </>;
  const key = result.unit_id || result.source_id || index;
  return result.source_id
    ? <div className="search-result-shell" key={key}><a className="search-result" href={`${API}/sources/${result.source_id}/file${result.page && result.source_type === "pdf" ? `#page=${result.page}` : ""}`} target="_blank" rel="noreferrer">{content}</a>{visiblePassages.length > 0 && <div className="search-result-passages">{visiblePassages.map((passage, passageIndex) => <p key={`${passage.unit_id || "passage"}-${passageIndex}`}>{readableSnippet(passage.snippet, result.source_type)}{passage.page ? ` · Page ${passage.page}` : ""}</p>)}</div>}<div className="search-result-actions">{passages.length > 1 && <button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? "Show fewer passages" : `Show ${passages.length - 1} more passages`}</button>}<button type="button" onClick={reveal}>Reveal in Finder</button>{revealMessage && <small>{revealMessage}</small>}</div></div>
    : <article className="search-result" key={key}>{content}</article>;
}

function readableSnippet(value, sourceType = "") {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  const readable = (text.match(/[\p{L}\p{N}]/gu) || []).length;
  if (readable >= 12 && readable / Math.max(text.length, 1) >= 0.45) return text;
  return sourceType === "image" ? "Image file" : "Text preview unavailable.";
}

function fileIcon(filename = "", sourceType = "") {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "pdf" || sourceType === "pdf") return "PDF";
  if (["jpg", "jpeg", "png", "webp"].includes(extension) || sourceType === "image") return "IMG";
  if (extension === "docx") return "DOC";
  return "TXT";
}
