import { useEffect, useRef, useState } from "react";
import { API, deleteSource, localDate, request, revealSource, sourceNeedsFinder } from "../api";

const SEARCH_MODES = ["keyword", "semantic", "visual", "hybrid", "hybrid_rerank"];

export default function SearchView({ initialQuery = "" }) {
  const [query, setQuery] = useState(initialQuery);
  const [mode, setMode] = useState("hybrid");
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [searched, setSearched] = useState(false);
  const [datePreset, setDatePreset] = useState("all");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
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
      const params = new URLSearchParams({ q: query.trim(), mode });
      if (datePreset === "today") {
        const today = localDate();
        params.set("from_date", today);
        params.set("to_date", nextDate(today));
      } else if (datePreset === "custom") {
        if (fromDate) params.set("from_date", fromDate);
        if (toDate) params.set("to_date", nextDate(toDate));
      }
      const payload = await request(`/search?${params.toString()}`);
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
      <div className="search-date-controls" aria-label="Search date range"><label>Added <select value={datePreset} onChange={(event) => setDatePreset(event.target.value)}><option value="all">All dates</option><option value="today">Today</option><option value="custom">Custom range</option></select></label>{datePreset === "custom" && <><label>From <input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} /></label><label>To <input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} /></label></>}</div>
    </form>
    {message && <div className="message error">{message}</div>}
    {results.length > 0 && <div className="search-results">
      <div className="search-results-heading"><span>{results.length} results</span><span>{mode}</span></div>
      {results.map((result, index) => <SearchResult key={result.source_id || result.unit_id || index} result={result} mode={mode} index={index} onDeleted={(sourceId) => setResults((current) => current.filter((item) => item.source_id !== sourceId))} />)}
    </div>}
    {!busy && !results.length && !message && <div className="memory-empty"><div className="empty-orbit">⌕</div><h3>{searched ? "No matching indexed file was found." : "Search your indexed files"}</h3><p>{searched ? "Try a filename, exact phrase, or a different retrieval mode." : "Find files and relevant passages with keyword, semantic, visual, or hybrid search."}</p></div>}
  </section>;
}

function SearchResult({ result, mode, index, onDeleted }) {
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

  async function openResult(event) {
    if (!sourceNeedsFinder(result.filename, result.source_type)) return;
    event.preventDefault();
    await reveal();
  }

  async function removeSource(event) {
    event.preventDefault();
    event.stopPropagation();
    if (!window.confirm(`Remove “${result.title || result.filename || "this source"}” and all indexed data?`)) return;
    try {
      await deleteSource(result.source_id);
      onDeleted?.(result.source_id);
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
    ? <div className="search-result-shell" key={key}><a className="search-result" onClick={openResult} href={result.capture_url || `${API}/sources/${result.source_id}/open${result.page && result.source_type === "pdf" ? `#page=${result.page}` : ""}`} target="_blank" rel="noreferrer">{content}</a>{visiblePassages.length > 0 && <div className="search-result-passages">{visiblePassages.map((passage, passageIndex) => <p key={`${passage.unit_id || "passage"}-${passageIndex}`}>{readableSnippet(passage.snippet, result.source_type)}{passage.page ? ` · Page ${passage.page}` : ""}</p>)}</div>}<div className="search-result-actions">{passages.length > 1 && <button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? "Show fewer passages" : `Show ${passages.length - 1} more passages`}</button>}{result.source_type !== "webpage" && <button type="button" onClick={reveal}>Reveal in Finder</button>}<button type="button" className="danger-icon" onClick={removeSource} aria-label={`Remove ${result.title || result.filename || "source"}`} title="Remove source">×</button>{revealMessage && <small>{revealMessage}</small>}</div></div>
    : <article className="search-result" key={key}>{content}</article>;
}

function readableSnippet(value, sourceType = "") {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  const readable = (text.match(/[\p{L}\p{N}]/gu) || []).length;
  if (readable >= 12 && readable / Math.max(text.length, 1) >= 0.45) return text;
  return sourceType === "image" ? "Image file" : "Text preview unavailable.";
}

function nextDate(value) {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() + 1);
  return date.toISOString().slice(0, 10);
}

function fileIcon(filename = "", sourceType = "") {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "pdf" || sourceType === "pdf") return "PDF";
  if (["jpg", "jpeg", "png", "webp"].includes(extension) || sourceType === "image") return "IMG";
  if (extension === "docx") return "DOC";
  if (extension === "pptx") return "PPT";
  if (extension === "md") return "MD";
  return "TXT";
}
