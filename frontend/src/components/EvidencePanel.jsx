import { useEffect, useRef, useState } from "react";
import { API, revealSource, sourceNeedsFinder } from "../api";

function readableExcerpt(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  const readable = (text.match(/[\p{L}\p{N}]/gu) || []).length;
  return readable >= 12 && readable / Math.max(text.length, 1) >= 0.45 ? text : "";
}

function groupCitations(citations) {
  const groups = new Map();
  for (const citation of citations) {
    const key = citation.source_id || citation.filename || citation.reference;
    const group = groups.get(key) || { ...citation, references: [], passages: [] };
    group.references.push(citation.reference);
    const passage = { unit_id: citation.unit_id, page: citation.page, excerpt: citation.excerpt };
    if (!group.passages.some((item) => item.unit_id === passage.unit_id)) group.passages.push(passage);
    if (!group.thumbnail_url && citation.thumbnail_url) group.thumbnail_url = citation.thumbnail_url;
    groups.set(key, group);
  }
  return Array.from(groups.values());
}

export default function EvidencePanel({ message, selectedReference = message?.selected_reference ?? null }) {
  const citations = message?.citations || [];
  const groups = groupCitations(citations);
  return <aside className="evidence-panel">
    <div className="evidence-heading"><div><span className="eyebrow">Context</span><h2>Evidence</h2></div><span className="evidence-count">{groups.length || 0}</span></div>
    {groups.length ? <><p className="evidence-intro">Verified sources supporting this response.</p><div className="evidence-list">{groups.map((citation) => <EvidenceCard key={citation.source_id || citation.reference} citation={citation} selected={selectedReference != null && citation.references.includes(selectedReference)} />)}</div></> : <div className="evidence-empty"><div>◌</div><strong>Select a source</strong><p>Verified citations from an assistant response will appear here.</p></div>}
  </aside>;
}

function EvidenceCard({ citation, selected = false }) {
  const [expanded, setExpanded] = useState(false);
  const [openMessage, setOpenMessage] = useState("");
  const cardRef = useRef(null);
  const image = citation.thumbnail_url ? `${API}${citation.thumbnail_url}` : null;
  const revealInFinder = sourceNeedsFinder(citation.filename, citation.source_type);
  const sourceUrl = citation.source_url?.startsWith("http")
    ? citation.source_url
    : `${API}${citation.source_url || `/sources/${citation.source_id}/open`}${citation.page && citation.source_type === "pdf" ? `#page=${citation.page}` : ""}`;
  const passages = citation.passages || [];
  const visiblePassages = expanded ? passages : passages.slice(0, 1);
  useEffect(() => {
    if (selected) cardRef.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [selected]);
  async function openSource(event) {
    if (!revealInFinder) return;
    event.preventDefault();
    setOpenMessage("");
    try {
      await revealSource(citation.source_id);
      setOpenMessage("Revealed in Finder");
    } catch (error) {
      setOpenMessage(error.message);
    }
  }
  return <article ref={cardRef} className={`evidence-card ${selected ? "selected" : ""}`}>
    {image ? <img src={image} alt="" /> : <div className="evidence-placeholder">{fileIcon(citation.filename, citation.source_type)}</div>}
    <div className="evidence-card-body"><div className="evidence-ref">[{citation.references.join(", ")}] · {citation.source_type || "source"}</div><h3>{citation.title || "Untitled source"}</h3><small>{citation.filename}{citation.page ? ` · Page ${citation.page}` : ""}{passages.length > 1 ? ` · ${passages.length} passages` : ""}</small>
      {visiblePassages.map((passage, index) => <p className="evidence-excerpt" key={`${passage.unit_id || "passage"}-${index}`}>{readableExcerpt(passage.excerpt) || "Text preview unavailable."}{passage.page && passage.page !== citation.page ? ` · Page ${passage.page}` : ""}</p>)}
      <div className="evidence-card-actions">{revealInFinder ? <button type="button" className="open-source" onClick={openSource}>Reveal in Finder ↗</button> : <a className="open-source" href={sourceUrl} target="_blank" rel="noreferrer">Open source ↗</a>}{citation.source_type !== "webpage" && <a className="source-download" href={`${API}/sources/${citation.source_id}/file?download=true`} target="_blank" rel="noreferrer">Download source ↧</a>}{passages.length > 1 && <button type="button" className="text-button" onClick={() => setExpanded(!expanded)}>{expanded ? "Show less" : `Show ${passages.length - 1} more`}</button>}{openMessage && <small>{openMessage}</small>}</div>
    </div>
  </article>;
}

function fileIcon(filename = "", sourceType = "") {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "pdf" || sourceType === "pdf") return "PDF";
  if (["jpg", "jpeg", "png", "webp"].includes(extension) || sourceType === "image") return "IMG";
  if (extension === "docx") return "DOC";
  if (extension === "pptx" || sourceType === "pptx") return "PPT";
  if (extension === "md" || sourceType === "markdown") return "MD";
  if (extension === "txt" || sourceType === "text") return "TXT";
  return "TXT";
}
