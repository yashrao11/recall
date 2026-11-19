export default function Sidebar({ collapsed, activeView, onNavigate, status, sources, conversations = [], selectedConversationId, onConversationSelect, onNewChat, onDeleteConversation, onNewContent, onToggle, dark, onThemeToggle }) {
  const readyCount = (status?.done || 0) + (status?.partial || 0);
  return (
    <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""}`}>
      <div className="sidebar-top">
        <div className="brand"><span className="brand-mark">R</span>{!collapsed && <div><strong>Recall</strong><small>private memory workspace</small></div>}</div>
        <button className="icon-button sidebar-collapse" onClick={onToggle} aria-label="Toggle sidebar">{collapsed ? "→" : "←"}</button>
      </div>
      {!collapsed && <>
        <button className="new-content" onClick={onNewContent}><span>＋</span> New content</button>
        <button className="sidebar-search" onClick={() => onNavigate("search")} aria-label="Quick search"><span>⌕</span><span>Quick search</span><kbd>⌘ K</kbd></button>
        <nav className="navigation">
          <button className={`nav-item ${activeView === "calendar" ? "active" : ""}`} onClick={() => onNavigate("calendar")}><span>◷</span> Calendar</button>
          <button className={`nav-item ${activeView === "search" ? "active" : ""}`} onClick={() => onNavigate("search")}><span>⌕</span> Search</button>
          <button className={`nav-item ${activeView === "chat" ? "active" : ""}`} onClick={() => onNavigate("chat")}><span>✦</span> Ask My Memory</button>
          <button className={`nav-item ${activeView === "connections" ? "active" : ""}`} onClick={() => onNavigate("connections")}><span>⌘</span> Connections</button>
          <button className={`nav-item ${activeView === "study" ? "active" : ""}`} onClick={() => onNavigate("study")}><span>✧</span> Study Mode</button>
        </nav>
        <div className="side-section conversation-section"><div className="section-heading"><span>Conversations</span><button className="section-action" onClick={onNewChat}>＋ New</button></div>{conversations.length ? conversations.slice(0, 8).map((conversation) => <div className={`conversation-item ${selectedConversationId === conversation.conversation_id ? "active" : ""}`} key={conversation.conversation_id}><button onClick={() => onConversationSelect(conversation.conversation_id)}><strong>{conversation.title || "New conversation"}</strong><small>{conversation.message_count || 0} messages</small></button><button className="conversation-delete" onClick={() => onDeleteConversation(conversation.conversation_id)} aria-label={`Delete ${conversation.title || "conversation"}`}>×</button></div>) : <div className="empty-side">Your saved conversations will appear here.</div>}</div>
      </>}
      <div className="sidebar-bottom">
        {!collapsed && <div className="indexing-card"><div><span>Indexing</span><strong>{status?.total ?? 0}</strong></div><small>{readyCount} ready · {(status?.pending || 0) + (status?.processing || 0)} in progress</small><div className="progress"><i style={{ width: `${status?.total ? (readyCount / status.total) * 100 : 0}%` }} /></div></div>}
        <button className="bottom-item" onClick={onThemeToggle}><span>{dark ? "☼" : "☾"}</span>{!collapsed && (dark ? "Light theme" : "Dark theme")}</button>
        {!collapsed && <div className="privacy-note"><span>◉</span><p>Local-first by default.<br />Cloud AI requires explicit permission.</p></div>}
      </div>
    </aside>
  );
}
