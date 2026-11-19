import { useEffect, useMemo, useRef, useState } from "react";
import { askMemory, fetchConversationMessages } from "../api";

const suggestions = [
  "What have I saved about deep learning?",
  "Explain transformers using my notes.",
  "Compare the research papers I saved about CNNs.",
  "Find my notes about system design.",
];

export default function ChatView({ conversationId, onConversationCreated, onEvidenceChange }) {
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState("");
  const [state, setState] = useState(null);
  const endRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setError(""); setState(null);
    if (!conversationId) { setMessages([]); setLoadingHistory(false); return undefined; }
    setLoadingHistory(true);
    fetchConversationMessages(conversationId).then((payload) => {
      if (!cancelled) setMessages(payload.messages || []);
    }).catch((reason) => { if (!cancelled) setError(reason.message); }).finally(() => {
      if (!cancelled) setLoadingHistory(false);
    });
    return () => { cancelled = true; };
  }, [conversationId, onEvidenceChange]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loading]);

  const activeTitle = useMemo(() => messages.find((item) => item.role === "user")?.content, [messages]);

  async function submit(event, suggestedQuestion = null) {
    event?.preventDefault();
    const content = (suggestedQuestion || question).trim();
    if (!content || loading) return;
    setQuestion(""); setError(""); setLoading(true);
    const optimistic = { message_id: `pending-${Date.now()}`, role: "user", content, created_at: new Date().toISOString() };
    setMessages((current) => [...current, optimistic]);
    try {
      const result = await askMemory(content, conversationId);
      const assistant = {
        message_id: result.assistant_message_id,
        role: "assistant",
        content: result.answer,
        citations: result.citations || [],
        created_at: new Date().toISOString(),
      };
      setMessages((current) => [...current, assistant]);
      setState(result.status);
      onEvidenceChange(assistant.citations.length ? assistant : null);
      if (!conversationId) onConversationCreated(result.conversation_id, activeTitle || content);
    } catch (reason) {
      setMessages((current) => current.filter((item) => item.message_id !== optimistic.message_id));
      setError(reason.message);
      setState("error");
    } finally { setLoading(false); }
  }

  const welcome = !conversationId && messages.length === 0;
  return <section className={`chat-view ${welcome ? "chat-welcome" : ""}`}>
    {welcome ? <div className="chat-hero">
      <span className="eyebrow">Ask My Memory</span>
      <h1>Your memory, one conversation away.</h1>
      <p>Ask questions about your indexed files. Recall grounds its answers in the evidence it can verify.</p>
      <div className="suggestions">{suggestions.map((item) => <button key={item} onClick={(event) => submit(event, item)}>{item}<span>↗</span></button>)}</div>
    </div> : <>
      <div className="chat-heading"><div><span className="eyebrow">Ask My Memory</span><h1>{activeTitle || "Conversation"}</h1><p>Grounded in your indexed memories · local conversation history</p></div><button className="quiet-button" onClick={() => onConversationCreated(null)}>＋ New chat</button></div>
      <div className="chat-thread">{loadingHistory ? <div className="chat-loading">Restoring conversation…</div> : messages.map((message) => <Message key={message.message_id} message={message} onEvidenceChange={onEvidenceChange} />)}{loading && <div className="message-row assistant"><div className="message-avatar">R</div><div className="assistant-bubble typing"><i /><i /><i /></div></div>}<div ref={endRef} /></div>
      {state && <StatusBanner state={state} />}
    </>}
    {error && <div className="chat-error">{error}</div>}
    <form className="composer" onSubmit={submit}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask something about your memories…" rows="1" disabled={loading} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(event); } }} /><button className="send-button" disabled={loading || !question.trim()} aria-label="Send question">↑</button><small>Enter to send · Shift + Enter for a new line</small></form>
  </section>;
}

function Message({ message, onEvidenceChange }) {
  const assistant = message.role === "assistant";
  return <div className={`message-row ${assistant ? "assistant" : "user"}`}>
    <div className="message-avatar">{assistant ? "R" : "Y"}</div>
    <div className={assistant ? "assistant-bubble" : "user-bubble"}>
      <div className="message-label">{assistant ? "Recall" : "You"}</div>
      {assistant ? <Markdown content={message.content} /> : <p>{message.content}</p>}
      {assistant && message.citations?.length > 0 && <div className="message-citations"><span>Sources</span>{message.citations.map((citation) => <button key={`${message.message_id}-${citation.reference}`} onClick={() => onEvidenceChange(message)}>[{citation.reference}] {citation.title}</button>)}</div>}
    </div>
  </div>;
}

function Markdown({ content }) {
  const blocks = String(content || "").split(/```/g);
  return <div className="markdown">{blocks.map((block, index) => {
    if (index % 2) return <pre key={index}><code>{block.replace(/^\w+\n/, "")}</code></pre>;
    return block.split(/\n\n+/).map((paragraph, paragraphIndex) => <p key={`${index}-${paragraphIndex}`}>
      {paragraph.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, partIndex) => {
        if (part.startsWith("**")) return <strong key={partIndex}>{part.slice(2, -2)}</strong>;
        if (part.startsWith("`")) return <code key={partIndex}>{part.slice(1, -1)}</code>;
        return part;
      })}
    </p>);
  })}</div>;
}

function StatusBanner({ state }) {
  const labels = { no_sources_indexed: "No sources are indexed yet. Capture or mount a file, then run indexing.", cloud_disabled: "Cloud AI is disabled. Your conversation is saved locally, but Recall cannot generate an answer until cloud AI is enabled.", insufficient_evidence: "Recall could not find enough indexed evidence to answer this question.", provider_error: "The generation service returned an error. No successful answer was shown.", provider_unavailable: "The configured generation provider is unavailable." };
  return labels[state] ? <div className={`chat-status ${state}`}>{labels[state]}</div> : null;
}
