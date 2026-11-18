import { API } from "../api";

export default function EvidencePanel({ message }) {
  const citations = message?.citations || [];
  return <aside className="evidence-panel">
    <div className="evidence-heading"><div><span className="eyebrow">Context</span><h2>Evidence</h2></div><span className="evidence-count">{citations.length || 0}</span></div>
    {citations.length ? <><p className="evidence-intro">Verified sources supporting this response.</p><div className="evidence-list">{citations.map((citation) => <EvidenceCard key={citation.reference} citation={citation} />)}</div></> : <div className="evidence-empty"><div>◌</div><strong>Select a source</strong><p>Verified citations from an assistant response will appear here.</p></div>}
  </aside>;
}

function EvidenceCard({ citation }) {
  const image = citation.thumbnail_url ? `${API}${citation.thumbnail_url}` : null;
  const sourcePath = citation.source_url || `/sources/${citation.source_id}/file`;
  const sourceUrl = sourcePath.startsWith("http") ? sourcePath : `${API}${sourcePath}`;
  return <a className="evidence-card" href={sourceUrl} target="_blank" rel="noreferrer">
    {image ? <img src={image} alt="" /> : <div className="evidence-placeholder">{citation.page ? `p.${citation.page}` : "R"}</div>}
    <div className="evidence-card-body"><div className="evidence-ref">[{citation.reference}] · {citation.source_type || "source"}</div><h3>{citation.title || "Untitled source"}</h3><small>{citation.filename}{citation.page ? ` · Page ${citation.page}` : ""}</small><p>{citation.excerpt || "Verified indexed evidence."}</p><span className="open-source">Open source ↗</span></div>
  </a>;
}
