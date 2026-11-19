import { useEffect, useState } from "react";
import {
  API,
  createRelationship,
  deleteRelationship,
  fetchEntities,
  fetchEntityDetail,
  fetchSourceConnections,
  reviewRelationship,
} from "../api";

const ENTITY_TYPE_PRIORITY = { person: 5, project: 5, organization: 5, place: 4, event: 4, concept: 3, topic: 2 };

function canonicalEntities(entities) {
  const grouped = new Map();
  for (const entity of entities) {
    const key = entity.canonical_title || entity.title?.trim().toLowerCase();
    const existing = grouped.get(key);
    if (!existing || (ENTITY_TYPE_PRIORITY[entity.object_type] || 0) > (ENTITY_TYPE_PRIORITY[existing.object_type] || 0)) {
      grouped.set(key, { ...entity, aliases: Array.from(new Set([...(existing?.aliases || []), ...(entity.aliases || []), ...(existing && existing.title !== entity.title ? [existing.title] : [])])) });
    } else if (existing) {
      existing.aliases = Array.from(new Set([...(existing.aliases || []), ...(entity.aliases || []), ...(existing.title !== entity.title ? [entity.title] : [])]));
      existing.source_count = Math.max(existing.source_count || 0, entity.source_count || 0);
      existing.relationship_count = Math.max(existing.relationship_count || 0, entity.relationship_count || 0);
    }
  }
  return Array.from(grouped.values());
}

const relationshipTypes = [
  ["related_to", "Related to"],
  ["similar_to", "Similar to"],
  ["part_of", "Part of"],
  ["depends_on", "Depends on"],
  ["created_by", "Created by"],
];

export default function ConnectionsView({ sources = [] }) {
  const [entities, setEntities] = useState([]);
  const [total, setTotal] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [minSources, setMinSources] = useState(2);
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [sourceDetail, setSourceDetail] = useState(null);
  const [sourceId, setSourceId] = useState("");
  const [includeUnsupported, setIncludeUnsupported] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(searchInput.trim()); setPage(0); }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchEntities({ query, type, status, minSources, limit: 24, offset: page * 24 })
      .then((payload) => {
        if (cancelled) return;
        setEntities(payload.entities || []);
        setTotal(payload.total || 0);
        if (payload.entities?.length && !payload.entities.some((entity) => entity.object_id === selectedId)) setSelectedId(payload.entities[0].object_id);
        if (!payload.entities?.length) { setSelectedId(null); setDetail(null); }
      })
      .catch((reason) => !cancelled && setError(reason.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [query, type, status, minSources, page]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setDetailLoading(true);
    setSourceDetail(null);
    fetchEntityDetail(selectedId, includeUnsupported)
      .then((payload) => !cancelled && setDetail(payload))
      .catch((reason) => !cancelled && setError(reason.message))
      .finally(() => !cancelled && setDetailLoading(false));
    return () => { cancelled = true; };
  }, [selectedId, includeUnsupported]);

  async function selectSource(value) {
    setSourceId(value);
    setSelectedId(null);
    setDetail(null);
    if (!value) { setSourceDetail(null); return; }
    setDetailLoading(true);
    setError("");
    try { setSourceDetail(await fetchSourceConnections(value, includeUnsupported)); }
    catch (reason) { setError(reason.message); }
    finally { setDetailLoading(false); }
  }

  async function refreshAfterMutation(message) {
    setNotice(message);
    if (selectedId) setDetail(await fetchEntityDetail(selectedId, includeUnsupported));
    if (sourceId) setSourceDetail(await fetchSourceConnections(sourceId, includeUnsupported));
    const payload = await fetchEntities({ query, type, status, minSources, limit: 24, offset: page * 24 });
    setEntities(payload.entities || []);
    setTotal(payload.total || 0);
  }

  async function handleReview(id, value) {
    try { await reviewRelationship(id, value); await refreshAfterMutation(`Relationship ${value}.`); }
    catch (reason) { setError(reason.message); }
  }

  async function handleDelete(id) {
    if (!window.confirm("Delete this user-created relationship?")) return;
    try { await deleteRelationship(id); await refreshAfterMutation("Relationship deleted."); }
    catch (reason) { setError(reason.message); }
  }

  function openSource(source) {
    if (source?.capture_url) window.open(source.capture_url, "_blank", "noopener,noreferrer");
    else if (source?.source_id) window.open(`${API}/sources/${source.source_id}/open`, "_blank", "noopener,noreferrer");
  }

  const source = sourceDetail?.source;
  const visibleEntities = canonicalEntities(entities);
  return <section className="connections-view">
    <div className="connections-header">
      <div><span className="eyebrow">Knowledge connections</span><h1>See how your memories connect.</h1><p>Explore entities and the evidence that links them across your indexed collection.</p></div>
      <button className="primary-button" onClick={() => setModalOpen(true)}>＋ Connect entities</button>
    </div>
    <div className="connections-toolbar">
      <form onSubmit={(event) => { event.preventDefault(); setQuery(searchInput.trim()); setPage(0); }} className="connection-search"><span>⌕</span><input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="Search entities or aliases" aria-label="Search entities" /></form>
      <select value={type} onChange={(event) => { setType(event.target.value); setPage(0); }} aria-label="Filter by entity type"><option value="">All types</option><option value="person">People</option><option value="project">Projects</option><option value="organization">Organizations</option><option value="place">Places</option><option value="event">Events</option><option value="topic">Topics</option><option value="concept">Concepts</option></select>
      <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(0); }} aria-label="Filter by status"><option value="">Active entities</option><option value="needs-review">Needs review</option><option value="inactive">Inactive</option></select>
      <select value={minSources} onChange={(event) => { setMinSources(Number(event.target.value)); setPage(0); }} aria-label="Filter by source support"><option value="2">Supported by 2+ sources</option><option value="1">Show single-source entities</option></select>
      <select value={sourceId} onChange={(event) => selectSource(event.target.value)} aria-label="Explore source"><option value="">Explore a source…</option>{sources.map((item) => <option key={item.source_id} value={item.source_id}>{item.title || item.filename || `Source ${item.source_id}`}</option>)}</select>
    </div>
    {notice && <div className="connection-notice">{notice}<button onClick={() => setNotice("")}>×</button></div>}
    {error && <div className="connection-error">{error}<button onClick={() => setError("")}>×</button></div>}
    <div className="connections-layout">
      <div className="connections-list-panel">
        <div className="panel-heading"><div><span className="eyebrow">Entities</span><strong>{total} discovered</strong></div><span>{page + 1} / {Math.max(1, Math.ceil(total / 24))}</span></div>
        {loading ? <div className="connection-state">Loading entities…</div> : visibleEntities.length ? <div className="entity-list">{visibleEntities.map((entity) => <button key={entity.object_id} className={`entity-card ${selectedId === entity.object_id ? "selected" : ""}`} onClick={() => { setSelectedId(entity.object_id); setSourceId(""); }}><span className="entity-type">{entity.object_type}</span><strong>{entity.title}</strong>{entity.aliases?.length > 0 && <small>{entity.aliases.slice(0, 2).join(" · ")}</small>}<span className="entity-meta">{entity.relationship_count || 0} connections · {entity.source_count || 0} supporting sources</span></button>)}</div> : <div className="connection-state"><strong>No meaningful entities found</strong><span>Try a different search, show single-source entities, or index more files.</span></div>}
        <div className="pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button disabled={(page + 1) * 24 >= total} onClick={() => setPage(page + 1)}>Next</button></div>
      </div>
      <div className="connection-detail-panel">
        {detailLoading ? <div className="connection-state">Loading connections…</div> : sourceDetail ? <SourceConnections source={source} data={sourceDetail} onEntity={setSelectedId} onOpenSource={openSource} onReview={handleReview} onDelete={handleDelete} /> : detail ? <EntityDetail detail={detail} includeUnsupported={includeUnsupported} onToggleUnsupported={() => setIncludeUnsupported(!includeUnsupported)} onEntity={setSelectedId} onReview={handleReview} onDelete={handleDelete} onOpenSource={openSource} /> : <div className="connection-state"><strong>Select an entity</strong><span>Choose an entity to inspect its supporting evidence and relationships.</span></div>}
      </div>
    </div>
    {modalOpen && <RelationshipModal entities={entities} onClose={() => setModalOpen(false)} onCreated={async () => { setModalOpen(false); await refreshAfterMutation("Relationship created."); }} onError={setError} />}
  </section>;
}

function EntityDetail({ detail, includeUnsupported, onToggleUnsupported, onEntity, onReview, onDelete, onOpenSource }) {
  const entity = detail.entity;
  return <div className="detail-scroll"><div className="detail-hero"><span className="entity-type">{entity.object_type}</span><h2>{entity.title}</h2><p>{entity.description || "No description available."}</p><div className="detail-badges"><span>{entity.status}</span><span>{entity.created_by}</span>{entity.aliases?.length > 0 && <span>Also known as {entity.aliases.join(", ")}</span>}</div></div><div className="detail-section"><div className="section-heading"><span>Supporting evidence</span><span>{detail.evidence?.length || 0}</span></div>{detail.evidence?.length ? detail.evidence.map((item) => <Evidence key={item.evidence_id} evidence={item} onOpenSource={onOpenSource} />) : <div className="small-empty">No supporting evidence is currently available.</div>}</div><div className="detail-section"><div className="section-heading"><span>Relationships</span><label className="toggle-label"><input type="checkbox" checked={includeUnsupported} onChange={onToggleUnsupported} /> Show reviewed exclusions</label></div>{detail.relationships?.length ? detail.relationships.map((item) => <Relationship key={item.relationship_id} item={item} onEntity={onEntity} onReview={onReview} onDelete={onDelete} onOpenSource={onOpenSource} />) : <div className="small-empty">No supported relationships yet.</div>}</div></div>;
}

function Relationship({ item, onEntity, onReview, onDelete, onOpenSource, showBoth = false }) {
  const other = item.direction === "incoming" ? item.from_entity : item.to_entity;
  const direction = item.direction === "symmetric" ? "↔" : item.direction === "incoming" ? "←" : "→";
  return <article className={`relationship-card ${item.supported === false ? "unsupported" : ""}`}><div className="relationship-line">{showBoth ? <><button className="link-button" onClick={() => onEntity(item.from_entity.object_id)}>{item.from_entity.title}</button><span className="relationship-arrow">{item.is_symmetric ? "↔" : "→"}</span><button className="link-button" onClick={() => onEntity(item.to_entity.object_id)}>{item.to_entity.title}</button></> : <><span className="relationship-arrow">{direction}</span><button className="link-button" onClick={() => onEntity(other.object_id)}>{other.title}</button></>}<span className="relationship-type">{item.relationship_type}</span></div><p>{item.explanation || "Connected through shared knowledge evidence."}</p><div className="relationship-meta"><span>{item.created_by} · {item.reviewed_status}</span><span>{item.supported === false ? "Unsupported" : `Evidence supported · ${item.supporting_source_count || item.evidence?.length || 0} source${(item.supporting_source_count || item.evidence?.length || 0) === 1 ? "" : "s"}`}</span></div>{item.evidence?.length > 0 && <div className="relationship-evidence">{item.evidence.map((evidence) => <Evidence key={evidence.evidence_id || evidence.source_id} evidence={evidence} onOpenSource={onOpenSource} compact />)}</div>}<div className="relationship-actions">{item.created_by === "system" || item.created_by === "llm" ? item.reviewed_status === "unreviewed" && <><button onClick={() => onReview(item.relationship_id, "confirmed")}>Confirm</button><button onClick={() => onReview(item.relationship_id, "rejected")}>Reject</button><button onClick={() => onReview(item.relationship_id, "unsupported")}>Mark unsupported</button></> : <button onClick={() => onDelete(item.relationship_id)}>Delete relationship</button>}</div></article>;
}

function Evidence({ evidence, onOpenSource, compact = false }) {
  const [expanded, setExpanded] = useState(false);
  const passages = evidence.passages || [{ page: evidence.page, excerpt: evidence.excerpt }];
  const visible = expanded ? passages : passages.slice(0, 1);
  return <div className={`evidence-item ${compact ? "compact" : ""}`}><div><strong>{evidence.source_title || `Source ${evidence.source_id}`}</strong>{evidence.page != null && <span>Page {evidence.page}</span>}{evidence.passage_count > 1 && <small>{evidence.passage_count} passages</small>}</div>{visible.map((passage, index) => <p key={`${passage.unit_id || "passage"}-${index}`}>{readableConnectionExcerpt(passage.excerpt) || "Text preview unavailable."}{passage.page != null && passage.page !== evidence.page ? ` · Page ${passage.page}` : ""}</p>)}<small>{evidence.discovery_method || "indexed evidence"}{evidence.confidence != null ? ` · ${Math.round(evidence.confidence * 100)}% confidence` : ""}</small><div className="evidence-item-actions">{evidence.source_id && <button className="text-button" onClick={() => onOpenSource({ source_id: evidence.source_id })}>Open source</button>}{passages.length > 1 && <button className="text-button" onClick={() => setExpanded(!expanded)}>{expanded ? "Show less" : `Show ${passages.length - 1} more`}</button>}</div></div>;
}

function readableConnectionExcerpt(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  const readable = (text.match(/[\p{L}\p{N}]/gu) || []).length;
  return readable >= 12 && readable / Math.max(text.length, 1) >= 0.45 ? text : "";
}

function SourceConnections({ source, data, onEntity, onOpenSource, onReview, onDelete }) {
  return <div className="detail-scroll"><div className="detail-hero"><span className="eyebrow">Source connections</span><h2>{source?.title || source?.filename}</h2><p>{source?.filename} · {source?.file_type || "file"}</p><button className="secondary-button" onClick={() => onOpenSource(source)}>Open source</button></div><div className="detail-section"><div className="section-heading"><span>Connected entities</span><span>{data.entities_total || 0}</span></div>{data.entities?.length ? data.entities.map((entity) => <button className="source-entity" key={entity.object_id} onClick={() => onEntity(entity.object_id)}><span className="entity-type">{entity.object_type}</span><strong>{entity.title}</strong><small>{entity.evidence?.length || 0} evidence item(s)</small></button>) : <div className="small-empty">No entities are connected to this source.</div>}</div><div className="detail-section"><div className="section-heading"><span>Relationships in this source</span><span>{data.relationships_total || 0}</span></div>{data.relationships?.length ? data.relationships.map((item) => <Relationship key={item.relationship_id} item={item} showBoth onEntity={onEntity} onReview={onReview} onDelete={onDelete} onOpenSource={onOpenSource} />) : <div className="small-empty">No relationships are supported by this source.</div>}</div></div>;
}

function RelationshipModal({ entities, onClose, onCreated, onError }) {
  const [from, setFrom] = useState(""); const [to, setTo] = useState(""); const [relationshipType, setRelationshipType] = useState("related_to"); const [busy, setBusy] = useState(false);
  async function submit(event) { event.preventDefault(); if (!from || !to || from === to) { onError("Choose two different entities."); return; } setBusy(true); try { await createRelationship({ from_object_id: Number(from), to_object_id: Number(to), relationship_type: relationshipType }); await onCreated(); } catch (reason) { onError(reason.message); } finally { setBusy(false); } }
  return <div className="modal-backdrop" onClick={onClose}><form className="connection-modal" onSubmit={submit} onClick={(event) => event.stopPropagation()}><div className="menu-heading"><div><span className="eyebrow">Manual connection</span><h2>Connect two entities</h2></div><button type="button" className="icon-button" onClick={onClose}>×</button></div><label>From entity<select value={from} onChange={(event) => setFrom(event.target.value)}><option value="">Choose entity…</option>{entities.map((entity) => <option key={entity.object_id} value={entity.object_id}>{entity.title} · {entity.object_type}</option>)}</select></label><label>Relationship<select value={relationshipType} onChange={(event) => setRelationshipType(event.target.value)}>{relationshipTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>To entity<select value={to} onChange={(event) => setTo(event.target.value)}><option value="">Choose entity…</option>{entities.map((entity) => <option key={entity.object_id} value={entity.object_id}>{entity.title} · {entity.object_type}</option>)}</select></label><div className="modal-actions"><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Connecting…" : "Create connection"}</button></div></form></div>;
}
