import { useState } from "react";

const NAVIGATION = [
  ["calendar", "Calendar", "◷"],
  ["search", "Search", "⌕"],
  ["chat", "Ask My Memory", "✦"],
  ["connections", "Connections", "⌘"],
  ["study", "Study Mode", "✧"],
];

export default function Sidebar({ collapsed, activeView, onNavigate, status, sources = [], conversations = [], selectedConversationId, onConversationSelect, onNewChat, onDeleteConversation, onNewContent, onToggle, dark, onThemeToggle }) {
  const readyCount = status?.ready ?? status?.done ?? 0;
  const partialCount = status?.partial || 0;
  const pendingCount = (status?.pending || 0) + (status?.processing || 0);
  const failedCount = (status?.failed || status?.errors || 0) + (status?.excluded || 0);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const uniqueSources = Array.from(new Map(sources.map((source) => [source.source_id, source])).values());
  return <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""}`}>
    <div className="sidebar-top">
      <div className="brand"><RecallMark />{!collapsed && <div><strong>Recall</strong><small>private memory workspace</small></div>}</div>
      <button className="icon-button sidebar-collapse" onClick={onToggle} aria-label="Toggle sidebar" title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>{collapsed ? "→" : "←"}</button>
    </div>
    <div className="sidebar-scroll">
      <button className="new-content" onClick={onNewContent} title="New content"><span>＋</span>{!collapsed && "New content"}</button>
      <nav className="navigation" aria-label="Primary navigation">
        {NAVIGATION.map(([value, label, icon]) => <button key={value} className={`nav-item ${activeView === value ? "active" : ""}`} onClick={() => onNavigate(value)} title={collapsed ? label : undefined} aria-label={label}><span className="nav-icon" aria-hidden="true">{icon}</span>{!collapsed && <span>{label}</span>}</button>)}
      </nav>
      {!collapsed && <div className="side-section conversation-section"><div className="section-heading"><span>Conversations</span><button className="section-action" onClick={onNewChat}>＋ New</button></div>{conversations.length ? conversations.map((conversation) => <div className={`conversation-item ${selectedConversationId === conversation.conversation_id ? "active" : ""}`} key={conversation.conversation_id}><button onClick={() => onConversationSelect(conversation.conversation_id)}><strong>{conversation.title || "New conversation"}</strong><small>{conversation.message_count || 0} messages</small></button><button className="conversation-delete" onClick={() => onDeleteConversation(conversation.conversation_id)} aria-label={`Delete ${conversation.title || "conversation"}`}>×</button></div>) : <div className="empty-side">Your saved conversations will appear here.</div>}</div>}
    </div>
    <div className="sidebar-bottom">
      {!collapsed && <><button className="indexing-card" onClick={() => setDetailsOpen(!detailsOpen)} aria-expanded={detailsOpen}><div><span>Indexing</span><strong>{status?.total ?? 0}</strong></div><small>{readyCount} ready · {partialCount} partial · {pendingCount} in progress{failedCount ? ` · ${failedCount} need attention` : ""}</small><div className="progress"><i style={{ width: `${status?.total ? ((readyCount + partialCount) / status.total) * 100 : 0}%` }} /></div><em>{detailsOpen ? "Hide file details" : "Show file details"}</em></button>{detailsOpen && <div className="indexing-details">{uniqueSources.length ? uniqueSources.map((source) => <div className="indexing-source" key={source.source_id}><span className={`status-dot ${source.status || source.indexing_status}`} /><div><strong>{source.filename || source.title || "Untitled source"}</strong><small>{source.source_type?.toUpperCase() || "FILE"} · {source.status || source.indexing_status}{source.indexing_error ? ` · ${statusDetail(source.indexing_error)}` : ""}</small></div></div>) : <small>No indexed sources yet.</small>}</div>}</>}
      <button className="bottom-item" onClick={onThemeToggle} title={dark ? "Light theme" : "Dark theme"}><span>{dark ? "☼" : "☾"}</span>{!collapsed && (dark ? "Light theme" : "Dark theme")}</button>
    </div>
  </aside>;
}

function statusDetail(value) {
  try {
    const parsed = JSON.parse(value);
    return parsed.errors?.[0] || parsed.warnings?.[0] || value;
  } catch {
    return value;
  }
}

function RecallMark() {
  return <svg className="brand-mark" viewBox="0 0 36 36" role="img" aria-label="Recall logo">
    <path d="M7 18c3.8-7 18.2-7 22 0-3.8 7-18.2 7-22 0Z" fill="none" stroke="currentColor" strokeWidth="2.2" />
    <circle cx="18" cy="18" r="3.6" fill="currentColor" />
    <path d="M10 8.5c2.4-2 5.1-3 8-3s5.6 1 8 3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity=".55" />
  </svg>;
}
