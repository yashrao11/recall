const collections = [
  ["Topics", "upcoming"], ["Projects", "upcoming"], ["People", "upcoming"], ["Events", "upcoming"],
  ["Ideas", "upcoming"], ["Questions", "upcoming"], ["Notes", "notes"], ["PDFs & documents", "documents"],
  ["Images & media", "images"], ["Weblinks", "webpages"], ["Collections", "upcoming"], ["Tags", "upcoming"],
];

export default function Sidebar({ collapsed, activeView, onNavigate, status, sources, conversations = [], selectedConversationId, onConversationSelect, onNewChat, onDeleteConversation, onNewContent, onToggle, dark, onThemeToggle }) {
  const counts = {
    notes: sources.filter((source) => ["text", "markdown"].includes(source.source_type)).length,
    documents: sources.filter((source) => ["pdf", "docx", "pptx"].includes(source.source_type)).length,
    images: sources.filter((source) => source.source_type === "image").length,
    webpages: 0,
  };
  return (
    <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""}`}>
      <div className="sidebar-top">
        <div className="brand"><span className="brand-mark">R</span>{!collapsed && <div><strong>Recall</strong><small>private memory workspace</small></div>}</div>
        <button className="icon-button sidebar-collapse" onClick={onToggle} aria-label="Toggle sidebar">{collapsed ? "→" : "←"}</button>
      </div>
      {!collapsed && <>
        <button className="workspace-selector"><span className="workspace-avatar">P</span><span><strong>Personal workspace</strong><small>Private collection</small></span><span className="chevron">⌄</span></button>
        <button className="new-content" onClick={onNewContent}><span>＋</span> New content</button>
        <div className="sidebar-search" onClick={() => onNavigate("search")}><span>⌕</span><span>Quick search</span><kbd>⌘ K</kbd></div>
        <nav className="navigation">
          <button className={`nav-item ${activeView === "calendar" ? "active" : ""}`} onClick={() => onNavigate("calendar")}><span>◷</span> Calendar</button>
          <button className={`nav-item ${activeView === "search" ? "active" : ""}`} onClick={() => onNavigate("search")}><span>⌕</span> Search</button>
          <button className={`nav-item ${activeView === "chat" ? "active" : ""}`} onClick={() => onNavigate("chat")}><span>✦</span> Ask My Memory</button>
          <button className={`nav-item ${activeView === "connections" ? "active" : ""}`} onClick={() => onNavigate("connections")}><span>⌘</span> Connections</button>
          <button className={`nav-item ${activeView === "study" ? "active" : ""}`} onClick={() => onNavigate("study")}><span>✧</span> Study Mode</button>
        </nav>
        <div className="side-section conversation-section"><div className="section-heading"><span>Conversations</span><button className="section-action" onClick={onNewChat}>＋ New</button></div>{conversations.length ? conversations.slice(0, 8).map((conversation) => <div className={`conversation-item ${selectedConversationId === conversation.conversation_id ? "active" : ""}`} key={conversation.conversation_id}><button onClick={() => onConversationSelect(conversation.conversation_id)}><strong>{conversation.title || "New conversation"}</strong><small>{conversation.message_count || 0} messages</small></button><button className="conversation-delete" onClick={() => onDeleteConversation(conversation.conversation_id)} aria-label={`Delete ${conversation.title || "conversation"}`}>×</button></div>) : <div className="empty-side">Your saved conversations will appear here.</div>}</div>
        <div className="side-section"><div className="section-heading"><span>Pinned</span><span className="section-hint">soon</span></div><div className="empty-side">Pin topics and collections here as your memory grows.</div></div>
        <div className="side-section collection-section"><div className="section-heading"><span>Objects</span><span className="section-hint">{sources.length}</span></div>{collections.map(([label, key]) => <button className={`collection-item ${key === "upcoming" ? "disabled" : ""}`} key={label} disabled={key === "upcoming"}><span className="collection-dot">{iconFor(label)}</span><span>{label}</span>{key !== "upcoming" && <strong>{counts[key] || 0}</strong>}{key === "upcoming" && <em>soon</em>}</button>)}</div>
      </>}
      <div className="sidebar-bottom">
        {!collapsed && <div className="indexing-card"><div><span>Indexing</span><strong>{status?.total ?? 0}</strong></div><small>{status?.done ?? 0} ready · {status?.pending ?? 0} pending</small><div className="progress"><i style={{ width: `${status?.total ? ((status.done || 0) / status.total) * 100 : 0}%` }} /></div></div>}
        <button className="bottom-item"><span>⚙</span>{!collapsed && "Settings"}<em>soon</em></button>
        <button className="bottom-item" onClick={onThemeToggle}><span>{dark ? "☼" : "☾"}</span>{!collapsed && (dark ? "Light theme" : "Dark theme")}</button>
        {!collapsed && <div className="privacy-note"><span>◉</span><p>Local-first by default.<br />Cloud AI requires explicit permission.</p></div>}
      </div>
    </aside>
  );
}

function iconFor(label) {
  return { Topics: "✦", Projects: "▣", People: "○", Events: "◷", Ideas: "✧", Questions: "?", Notes: "▤", "PDFs & documents": "▧", "Images & media": "▧", Weblinks: "↗", Collections: "◫", Tags: "#" }[label] || "•";
}
