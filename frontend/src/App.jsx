import { useCallback, useEffect, useMemo, useState } from "react";
import { API, deleteConversation, fetchCalendar, fetchConversations, localDate, request, shiftPeriod } from "./api";
import Sidebar from "./components/Sidebar";
import ContextPanel from "./components/ContextPanel";
import MiniCalendar, { eventDatesForMonth } from "./components/MiniCalendar";
import { MemoryWall } from "./components/MemoryCard";
import SearchView from "./components/SearchView";
import ChatView from "./components/ChatView";
import EvidencePanel from "./components/EvidencePanel";
import ConnectionsView from "./components/ConnectionsView";
import StudyView from "./components/StudyView";
import CaptureMenu from "./components/CaptureMenu";

const views = ["month", "week", "3-days", "day"];
const displayModes = ["wall", "gallery", "list", "timeline"];

export default function App() {
  const today = localDate();
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const [activeView, setActiveView] = useState("calendar");
  const [calendarView, setCalendarView] = useState("month");
  const [displayMode, setDisplayMode] = useState("wall");
  const [selectedDate, setSelectedDate] = useState(today);
  const [month, setMonth] = useState(today.slice(0, 7));
  const [calendarData, setCalendarData] = useState({ events: [], days: [], statistics: {} });
  const [dayData, setDayData] = useState({ events: [], days: [], statistics: {} });
  const [monthData, setMonthData] = useState({ events: [], days: [] });
  const [onThisDay, setOnThisDay] = useState([]);
  const [recap, setRecap] = useState(null);
  const [sources, setSources] = useState([]);
  const [status, setStatus] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const [contextOpen, setContextOpen] = useState(true);
  const [dark, setDark] = useState(false);
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [conversations, setConversations] = useState([]);
  const [selectedConversationId, setSelectedConversationId] = useState(null);
  const [selectedAssistant, setSelectedAssistant] = useState(null);

  const eventDates = useMemo(() => eventDatesForMonth(monthData.days), [monthData.days]);

  useEffect(() => { refreshSources(); refreshStatus(); refreshMonth(month); refreshConversations(); }, []);
  useEffect(() => {
    function handleShortcut(event) {
      const target = event.target;
      const editing = target instanceof HTMLElement && target.matches("input, textarea, select, [contenteditable='true']");
      if (event.metaKey && event.key.toLowerCase() === "k" && !editing) {
        event.preventDefault();
        setActiveView("search");
        window.dispatchEvent(new Event("recall:focus-search"));
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);
  useEffect(() => { refreshCalendar(calendarView, calendarView === "month" ? month : selectedDate); refreshCalendar("day", selectedDate, setDayData); refreshContext(selectedDate); }, [calendarView, selectedDate, month]);

  async function refreshStatus() { try { setStatus(await request("/index/status")); } catch (error) { setMessage(error.message); } }
  async function refreshSources() { try { const payload = await request("/sources"); setSources(payload.sources || []); } catch (error) { setMessage(error.message); } }
  async function refreshConversations() { try { const payload = await fetchConversations(); setConversations(payload.conversations || []); } catch (error) { setMessage(error.message); } }
  async function refreshMonth(value) { try { setMonthData(await fetchCalendar("month", value, timezone)); } catch (error) { setMessage(error.message); } }
  async function refreshCalendar(view, date, setter = setCalendarData) { try { setter(await fetchCalendar(view, date, timezone)); } catch (error) { setMessage(error.message); } }
  async function refreshContext(date) {
    try {
      const [history, weekly] = await Promise.all([request(`/calendar/on-this-day?date=${date}&timezone=${encodeURIComponent(timezone)}`), request(`/calendar/recap?period=week&date=${date}&timezone=${encodeURIComponent(timezone)}`)]);
      setOnThisDay(history.events || history || []); setRecap(weekly);
    } catch (error) { setMessage(error.message); }
  }
  function selectDate(date) { setSelectedDate(date); setMonth(date.slice(0, 7)); }
  function movePeriod(direction) { if (activeView !== "calendar") return; const next = shiftPeriod(selectedDate, calendarView, direction); setSelectedDate(next); setMonth(calendarView === "month" ? next : next.slice(0, 7)); }
  function goToday() { selectDate(today); }
  function openChat() { setActiveView("chat"); }
  function newChat() { setSelectedConversationId(null); setSelectedAssistant(null); setActiveView("chat"); }
  function selectConversation(id) { setSelectedConversationId(id); setSelectedAssistant(null); setActiveView("chat"); }
  async function removeConversation(id) { try { await deleteConversation(id); if (selectedConversationId === id) newChat(); await refreshConversations(); } catch (error) { setMessage(error.message); } }
  const handleConversationCreated = useCallback((id) => { setSelectedConversationId(id); setActiveView("chat"); refreshConversations(); }, []);
  const handleEvidenceChange = useCallback((assistant) => setSelectedAssistant(assistant), []);
  async function refreshAfterCapture() {
    await Promise.all([refreshSources(), refreshStatus(), refreshMonth(month), refreshCalendar(calendarView, calendarView === "month" ? month : selectedDate), refreshContext(selectedDate)]);
  }
  function openEvent(event) { if (event.source_id) window.open(`${API}/sources/${event.source_id}/file`, "_blank", "noopener,noreferrer"); }
  const activeEvents = (calendarView === "month" ? dayData.events : calendarData.events) || [];
  const heading = headingFor(selectedDate, activeEvents);

  return <div className={`app ${dark ? "dark" : ""}`}>
    <div className={`shell ${collapsed ? "shell-collapsed" : ""}`}>
      <Sidebar collapsed={collapsed} activeView={activeView} onNavigate={setActiveView} status={status} sources={sources} conversations={conversations} selectedConversationId={selectedConversationId} onConversationSelect={selectConversation} onDeleteConversation={removeConversation} onNewChat={newChat} onNewContent={() => setNewMenuOpen(true)} onToggle={() => setCollapsed(!collapsed)} dark={dark} onThemeToggle={() => setDark(!dark)} />
      <main className="main">
        <header className="topbar"><div className="topbar-left"><button className="icon-button mobile-toggle" onClick={() => setCollapsed(!collapsed)} aria-label="Toggle navigation">☰</button><button className="icon-button" onClick={() => activeView === "chat" ? newChat() : movePeriod(-1)} aria-label="Previous period or new chat">‹</button><button className="icon-button" onClick={() => activeView === "chat" ? openChat() : movePeriod(1)} aria-label="Next period">›</button><div className="breadcrumb"><span>Personal workspace</span><b>/</b><strong>{activeView === "search" ? "Search" : activeView === "chat" ? "Ask My Memory" : activeView === "connections" ? "Connections" : activeView === "study" ? "Study Mode" : "Calendar"}</strong>{activeView === "calendar" && <><b>/</b><span>{formatDate(selectedDate, { month: "short", day: "numeric" })}</span></>}</div></div><div className="top-actions">{activeView === "calendar" && <><button className="today-button" onClick={goToday}>Today</button><button className="context-toggle" onClick={() => setContextOpen(!contextOpen)}>{contextOpen ? "Hide context" : "Show context"}</button></>}{activeView === "chat" && <button className="context-toggle" onClick={() => setContextOpen(!contextOpen)}>{contextOpen ? "Hide evidence" : "Show evidence"}</button>}{activeView === "connections" && <button className="context-toggle" onClick={() => setContextOpen(!contextOpen)}>{contextOpen ? "Hide context" : "Show context"}</button>}{activeView === "search" && <span className="connection"><i /> Local API</span>}</div></header>
        {message && <div className="message">{message}<button onClick={() => setMessage("")}>×</button></div>}
        {activeView === "search" ? <SearchView /> : activeView === "chat" ? <ChatView conversationId={selectedConversationId} onConversationCreated={handleConversationCreated} onEvidenceChange={handleEvidenceChange} /> : activeView === "connections" ? <ConnectionsView sources={sources} /> : activeView === "study" ? <StudyView sources={sources} /> : <section className="calendar-view"><div className="calendar-heading"><div><span className="eyebrow">{activeEvents.length || 0} memories in view</span><h1>{heading}</h1><p>{formatDate(selectedDate, { weekday: "long", month: "long", day: "numeric", year: "numeric" })} · {timezone}</p></div><div className="view-tools"><div className="view-tabs">{views.map((view) => <button className={calendarView === view ? "selected" : ""} key={view} onClick={() => setCalendarView(view)}>{view === "3-days" ? "3 Days" : view[0].toUpperCase() + view.slice(1)}</button>)}</div><div className="display-tabs">{displayModes.map((mode) => <button className={displayMode === mode ? "selected" : ""} key={mode} onClick={() => setDisplayMode(mode)}>{mode[0].toUpperCase() + mode.slice(1)}</button>)}</div></div></div><div className="memory-summary"><span>{activeEvents.length ? `${activeEvents.length} recorded ${activeEvents.length === 1 ? "memory" : "memories"}` : "A quiet day"}</span>{calendarData.statistics?.file_types && Object.keys(calendarData.statistics.file_types).length > 0 && <span>{Object.entries(calendarData.statistics.file_types).map(([type, count]) => `${count} ${type}`).join(" · ")}</span>}</div><MemoryWall events={activeEvents} mode={displayMode} onOpen={openEvent} /></section>}
      </main>
      {contextOpen && (activeView === "chat" ? <EvidencePanel message={selectedAssistant} /> : activeView === "calendar" ? <ContextPanel selectedDate={selectedDate} month={month} eventDates={eventDates} onSelect={selectDate} onMonthChange={(direction) => { const next = shiftPeriod(`${month}-01`, "month", direction).slice(0, 7); setMonth(next); refreshMonth(next); }} onThisDay={onThisDay} recap={recap} /> : activeView === "connections" ? <div className="context-panel context-placeholder"><span className="eyebrow">Knowledge context</span><h2>Follow the evidence.</h2><p>Select an entity or source to inspect supported connections. Relationships are labelled by their origin and review status.</p></div> : activeView === "study" ? <div className="context-panel context-placeholder"><span className="eyebrow">Study Mode</span><h2>Spaced repetition.</h2><p>Cards are scheduled with the SM-2 algorithm. Answer cards honestly — your ratings determine when each card comes back.</p></div> : <div className="context-panel context-placeholder"><span className="eyebrow">Workspace</span><h2>Search results</h2><p>Select a result to open its source.</p></div>)}
    </div>
    {newMenuOpen && <CaptureMenu onClose={() => setNewMenuOpen(false)} onCaptured={refreshAfterCapture} />}
  </div>;
}

function formatDate(date, options) { return new Date(`${date}T12:00:00`).toLocaleDateString(undefined, options); }
function headingFor(date, events) { const bases = new Set((events || []).map((event) => event.date_basis)); if (bases.size === 1 && bases.has("modified_at")) return `Updated in Recall on ${formatDate(date, { month: "long", day: "numeric" })}`; if (bases.size === 1 && bases.has("captured_at")) return `Captured on ${formatDate(date, { month: "long", day: "numeric" })}`; if (bases.size === 1 && bases.has("imported_at")) return `Added to Recall on ${formatDate(date, { month: "long", day: "numeric" })}`; return `Memory activity on ${formatDate(date, { month: "long", day: "numeric" })}`; }
