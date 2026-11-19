import { API } from "../api";

const labels = {
  file_imported: "Imported",
  file_modified: "Modified",
  note_created: "Note",
  webpage_saved: "Webpage",
  screenshot_captured: "Screenshot",
  study_session_completed: "Study session",
  memory_revisited: "Revisited",
};

export default function MemoryCard({ event, mode = "wall", onOpen, onDelete }) {
  const title = event.title || event.metadata?.filename || "Untitled memory";
  const preview = event.metadata?.preview || event.metadata?.text_preview || event.metadata?.description;
  const image = event.thumbnail_url ? `${API}${event.thumbnail_url}` : null;
  return <article className={`memory-card ${mode} ${image ? "has-image" : ""}`} onClick={() => event.source_id && onOpen(event)}>
    {event.source_id && <button className="memory-delete" type="button" onClick={(clickEvent) => { clickEvent.stopPropagation(); onDelete?.(event); }} aria-label={`Remove ${title}`} title="Remove from Recall">×</button>}
    {image ? <img className="memory-image" src={image} alt="" /> : <div className="memory-placeholder"><span>{event.file_type === "pdf" ? "▧" : event.event_type === "note_created" ? "▤" : "✦"}</span></div>}
    <div className="memory-card-content">
      <div className="memory-card-meta"><span className="event-badge">{labels[event.event_type] || event.event_type}</span>{event.date_basis && <span className="date-basis">{basisLabel(event.date_basis)}</span>}</div>
      <h3>{title}</h3>
      {preview && <p>{preview}</p>}
      <div className="memory-footer"><span>{formatTime(event.timestamp)}</span><span>{event.file_type || event.event_type.replaceAll("_", " ")}</span>{event.page && <span>Page {event.page}</span>}{event.metadata?.visual_match && <span className="visual-pill">Visual match</span>}</div>
    </div>
  </article>;
}

export function MemoryWall({ events, mode, onOpen, onDelete }) {
  if (!events?.length) return <div className="memory-empty"><div className="empty-orbit">✦</div><h3>No memories on this date</h3><p>Import a folder or create supported content to see it appear here.</p></div>;
  return <div className={`memory-wall ${mode}`}>{events.map((event) => <MemoryCard key={event.event_id} event={event} mode={mode} onOpen={onOpen} onDelete={onDelete} />)}</div>;
}

function formatTime(value) { return value ? new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""; }
function basisLabel(value) { return ({ imported_at: "Imported", modified_at: "Modified", captured_at: "Captured", studied_at: "Studied", revisited_at: "Revisited" }[value] || value.replaceAll("_", " ")); }
