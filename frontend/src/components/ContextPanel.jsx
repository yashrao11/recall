import MiniCalendar from "./MiniCalendar";

export default function ContextPanel({ selectedDate, month, eventDates, onSelect, onMonthChange, onThisDay, recap }) {
  return <aside className="context-panel">
    <MiniCalendar selectedDate={selectedDate} month={month} eventDates={eventDates} onSelect={onSelect} onMonthChange={onMonthChange} />
    <section className="context-section"><div className="context-heading"><span>On this day</span><small>Previous years</small></div>{onThisDay?.length ? <div className="context-events">{onThisDay.slice(0, 4).map((event) => <div className="context-event" key={event.event_id}><span className="context-dot" /><div><strong>{event.title || "Untitled memory"}</strong><small>{event.date} · {event.event_type.replaceAll("_", " ")}</small></div></div>)}</div> : <p className="context-empty">No historical memories for this date.</p>}</section>
    <section className="context-section"><div className="context-heading"><span>Weekly recap</span><small>{recap?.date || "This week"}</small></div><div className="recap-grid"><Stat label="Captured" value={recap?.statistics?.captured_files} /><Stat label="Notes" value={recap?.statistics?.created_notes} /><Stat label="Webpages" value={recap?.statistics?.saved_webpages} /><Stat label="Memories" value={recap?.statistics?.memory_count} /></div>{Object.keys(recap?.statistics?.file_types || {}).length > 0 && <div className="file-types">{Object.entries(recap.statistics.file_types).map(([type, count]) => <span key={type}>{type} <b>{count}</b></span>)}</div>}</section>
  </aside>;
}

function Stat({ label, value = 0 }) { return <div className="recap-stat"><strong>{value}</strong><span>{label}</span></div>; }
