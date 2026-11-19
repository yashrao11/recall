import { useState } from "react";
import { API } from "../api";

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

export default function EvidencePanel({ message }) {
  const citations = message?.citations || [];
  const groups = groupCitations(citations);
  return <aside className="evidence-panel">
    <div className="evidence-heading"><div><span className="eyebrow">Context</span><h2>Evidence</h2></div><span className="evidence-count">{groups.length || 0}</span></div>
    {groups.length ? <><p className="evidence-intro">Verified sources supporting this response.</p><div className="evidence-list">{groups.map((citation) => <EvidenceCard key={citation.source_id || citation.reference} citation={citation} />)}</div></> : <div className="evidence-empty"><div>◌</div><strong>Select a source</strong><p>Verified citations from an assistant response will appear here.</p></div>}
  </aside>;
}

function EvidenceCard({ citation }) {
  const [expanded, setExpanded] = useState(false);
  const image = citation.thumbnail_url ? `${API}${citation.thumbnail_url}` : null;
  const sourceUrl = citation.source_url?.startsWith("http")
    ? citation.source_url
    : `${API}${citation.source_url || `/sources/${citation.source_id}/open`}${citation.page && citation.source_type === "pdf" ? `#page=${citation.page}` : ""}`;
  const passages = citation.passages || [];
  const visiblePassages = expanded ? passages : passages.slice(0, 1);
  return <article className="evidence-card">
    {image ? <img src={image} alt="" /> : <div className="evidence-placeholder">{fileIcon(citation.filename, citation.source_type)}</div>}
    <div className="evidence-card-body"><div className="evidence-ref">[{citation.references.join(", ")}] · {citation.source_type || "source"}</div><h3>{citation.title || "Untitled source"}</h3><small>{citation.filename}{citation.page ? ` · Page ${citation.page}` : ""}{passages.length > 1 ? ` · ${passages.length} passages` : ""}</small>
      {visiblePassages.map((passage, index) => <p className="evidence-excerpt" key={`${passage.unit_id || "passage"}-${index}`}>{readableExcerpt(passage.excerpt) || "Text preview unavailable."}{passage.page && passage.page !== citation.page ? ` · Page ${passage.page}` : ""}</p>)}
      <div className="evidence-card-actions"><a className="open-source" href={sourceUrl} target="_blank" rel="noreferrer">Open source ↗</a>{passages.length > 1 && <button type="button" className="text-button" onClick={() => setExpanded(!expanded)}>{expanded ? "Show less" : `Show ${passages.length - 1} more`}</button>}</div>
    </div>
  </article>;
}

function fileIcon(filename = "", sourceType = "") {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "pdf" || sourceType === "pdf") return "PDF";
  if (["jpg", "jpeg", "png", "webp"].includes(extension) || sourceType === "image") return "IMG";
  if (extension === "docx") return "DOC";
  return "TXT";
}
