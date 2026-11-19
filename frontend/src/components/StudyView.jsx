import { useEffect, useRef, useState } from "react";
import {
  API,
  completeStudySession,
  fetchDueCards,
  fetchEntities,
  generateStudySet,
  respondToCard,
} from "../api";

// SM-2 quality labels
const QUALITY_LABELS = [
  { value: 0, label: "Blackout", hint: "Complete blank" },
  { value: 1, label: "Wrong", hint: "Incorrect, but familiar" },
  { value: 2, label: "Hard", hint: "Incorrect after hint" },
  { value: 3, label: "Good", hint: "Correct with effort" },
  { value: 4, label: "Easy", hint: "Correct with hesitation" },
  { value: 5, label: "Perfect", hint: "Instant recall" },
];

export default function StudyView({ sources = [] }) {
  const [mode, setMode] = useState("home"); // home | generate | review
  const [sourceId, setSourceId] = useState("");
  const [objectId, setObjectId] = useState("");
  const [entities, setEntities] = useState([]);
  const [generating, setGenerating] = useState(false);
  const [cards, setCards] = useState([]);
  const [evidence, setEvidence] = useState([]);
  const [title, setTitle] = useState("");
  const [statusMessage, setStatusMessage] = useState(null);
  const [error, setError] = useState("");
  const [dueCards, setDueCards] = useState([]);
  const [dueLoading, setDueLoading] = useState(false);

  // Review session state
  const [reviewCards, setReviewCards] = useState([]);
  const [reviewIndex, setReviewIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [sessionId] = useState(null);
  const [answered, setAnswered] = useState(0);
  const [correct, setCorrect] = useState(0);
  const [sessionComplete, setSessionComplete] = useState(false);
  const [sessionResult, setSessionResult] = useState(null);
  const startedAt = useRef(null);

  useEffect(() => {
    if (mode === "home") {
      loadDueCards();
    }
  }, [mode]);

  useEffect(() => {
    fetchEntities({ limit: 100 }).then((payload) => setEntities(payload.entities || [])).catch(() => setEntities([]));
  }, []);

  async function loadDueCards() {
    setDueLoading(true);
    try {
      const payload = await fetchDueCards(30);
      setDueCards(payload.cards || []);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setDueLoading(false);
    }
  }

  async function handleGenerate(event) {
    event.preventDefault();
    if (!sourceId) return;
    setGenerating(true);
    setError("");
    setStatusMessage(null);
    try {
      const payload = await generateStudySet({
        sourceId: sourceId ? parseInt(sourceId, 10) : null,
        objectId: objectId ? parseInt(objectId, 10) : null,
      });
      if (payload.status === "ready") {
        setCards(payload.cards || []);
        setEvidence(payload.evidence || []);
        setTitle(payload.title || "Study set");
        setMode("generate");
      } else if (payload.status === "cloud_disabled") {
        setStatusMessage({ kind: "cloud_disabled", text: "Cloud AI is disabled. Enable cloud AI in settings to generate study material from your documents." });
      } else if (payload.status === "insufficient_evidence") {
        setStatusMessage({ kind: "insufficient", text: "This source does not have enough indexed content to generate study material yet. Try running indexing first." });
      } else {
        setStatusMessage({ kind: "error", text: payload.message || "Study generation failed." });
      }
    } catch (reason) {
      setError(reason.message);
    } finally {
      setGenerating(false);
    }
  }

  function startReview(cardsToReview) {
    startedAt.current = new Date().toISOString();
    setReviewCards(cardsToReview);
    setReviewIndex(0);
    setFlipped(false);
    setAnswered(0);
    setCorrect(0);
    setSessionComplete(false);
    setSessionResult(null);
    setMode("review");
  }

  async function handleRespond(quality) {
    const card = reviewCards[reviewIndex];
    if (!card) return;
    try {
      await respondToCard(card.card_id, quality, sessionId);
    } catch {
      // non-blocking — session still advances
    }
    const nextAnswered = answered + 1;
    const nextCorrect = correct + (quality >= 3 ? 1 : 0);
    setAnswered(nextAnswered);
    setCorrect(nextCorrect);

    if (reviewIndex + 1 >= reviewCards.length) {
      // Session done — persist
      const completedAt = new Date().toISOString();
      let result = null;
      try {
        result = await completeStudySession({
          sourceId: sourceId ? parseInt(sourceId, 10) : null,
          objectId: objectId ? parseInt(objectId, 10) : null,
          startedAt: startedAt.current,
          completedAt,
          questionsAttempted: nextAnswered,
          answersCorrect: nextCorrect,
        });
      } catch {
        // non-blocking
      }
      setSessionResult(result);
      setSessionComplete(true);
    } else {
      setReviewIndex(reviewIndex + 1);
      setFlipped(false);
    }
  }

  if (mode === "review") {
    return (
      <ReviewSession
        cards={reviewCards}
        index={reviewIndex}
        flipped={flipped}
        onFlip={() => setFlipped(true)}
        onRespond={handleRespond}
        answered={answered}
        correct={correct}
        complete={sessionComplete}
        result={sessionResult}
        onDone={() => { setMode("home"); loadDueCards(); }}
      />
    );
  }

  return (
    <div className="study-view">
      <div className="study-header">
        <div>
          <span className="eyebrow">Study Mode</span>
          <h1>Learn from your memory</h1>
          <p>Generate grounded flashcards and quizzes from your indexed documents, or review cards that are due.</p>
        </div>
      </div>

      {error && (
        <div className="study-notice study-error">
          {error}
          <button onClick={() => setError("")}>×</button>
        </div>
      )}

      <div className="study-layout">
        {/* Generate panel */}
        <section className="study-panel">
          <div className="study-panel-heading">
            <span>✦</span>
            <strong>Generate a study set</strong>
          </div>
          <p className="study-panel-desc">
            Choose one of your indexed sources and Recall will generate flashcards, multiple-choice questions, and short-answer items grounded in that document's content.
          </p>
          <form className="study-form" onSubmit={handleGenerate}>
            <label>
              Source
              <select value={sourceId} onChange={(e) => { setSourceId(e.target.value); setObjectId(""); }} disabled={Boolean(objectId)}>
                <option value="">Select a source…</option>
                {sources
                  .filter((s) => s.indexing_status === "done")
                  .map((s) => (
                    <option key={s.source_id} value={s.source_id}>
                      {s.title || s.original_path}
                    </option>
                  ))}
              </select>
            </label>
            <div className="study-or-divider"><span>or study a topic</span></div>
            <label>
              Topic or entity
              <select value={objectId} onChange={(e) => { setObjectId(e.target.value); setSourceId(""); }} disabled={Boolean(sourceId)}>
                <option value="">Select an indexed topic…</option>
                {entities.map((entity) => <option key={entity.object_id} value={entity.object_id}>{entity.title} · {entity.object_type}</option>)}
              </select>
            </label>
            <button className="primary-button study-generate-btn" disabled={generating || (!sourceId && !objectId)}>
              {generating ? "Generating…" : "Generate study set"}
            </button>
          </form>
          {statusMessage && (
            <div className={`study-notice ${statusMessage.kind === "cloud_disabled" ? "study-cloud-disabled" : statusMessage.kind === "insufficient" ? "study-insufficient" : "study-error"}`}>
              {statusMessage.text}
            </div>
          )}
        </section>

        {/* Due cards panel */}
        <section className="study-panel">
          <div className="study-panel-heading">
            <span>◷</span>
            <strong>Review due cards</strong>
          </div>
          <p className="study-panel-desc">
            Cards are scheduled using spaced repetition (SM-2). These are due for review today.
          </p>
          {dueLoading ? (
            <div className="study-loading">Loading due cards…</div>
          ) : dueCards.length === 0 ? (
            <div className="study-empty">
              <span>✓</span>
              <strong>All caught up</strong>
              <p>No cards are due right now. Generate a new study set or check back later.</p>
            </div>
          ) : (
            <div className="study-due-summary">
              <strong className="study-due-count">{dueCards.length}</strong>
              <span>card{dueCards.length !== 1 ? "s" : ""} due</span>
              <button className="primary-button study-review-btn" onClick={() => startReview(dueCards)}>
                Start review →
              </button>
            </div>
          )}
        </section>
      </div>

      {/* Generated cards preview */}
      {mode === "generate" && cards.length > 0 && (
        <div className="study-generated">
          <div className="study-generated-header">
            <div>
              <span className="eyebrow">Generated study set</span>
              <h2>{title}</h2>
              <p>{cards.length} items · {evidence.length} source reference{evidence.length !== 1 ? "s" : ""}</p>
            </div>
            <button className="primary-button study-review-btn" onClick={() => startReview(cards)}>
              Review all →
            </button>
          </div>
          <div className="study-cards-grid">
            {cards.map((card) => (
              <CardPreview key={card.card_id} card={card} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CardPreview({ card }) {
  const [open, setOpen] = useState(false);
  const typeLabel = { flashcard: "Flashcard", mcq: "Multiple choice", short_answer: "Short answer" }[card.card_type] || card.card_type;
  return (
    <div className={`study-card-preview ${open ? "open" : ""}`}>
      <div className="study-card-top">
        <span className="study-card-type">{typeLabel}</span>
        <button className="study-card-toggle" onClick={() => setOpen(!open)} aria-label={open ? "Collapse" : "Expand"}>
          {open ? "−" : "+"}
        </button>
      </div>
      <p className="study-card-front">{card.front}</p>
      {open && (
        <>
          <p className="study-card-back">{card.back}</p>
          {card.explanation && <p className="study-card-explanation"><strong>Why:</strong> {card.explanation}</p>}
          {card.options && (
            <ul className="study-card-options">
              {card.options.map((opt, i) => (
                <li key={i} className={opt === card.back ? "correct" : ""}>{opt}</li>
              ))}
            </ul>
          )}
          {card.evidence?.length > 0 && (
            <div className="study-card-evidence">
              {card.evidence.map((ev, i) => (
                <a
                  key={i}
                  href={`${API}/sources/${ev.source_id}/file`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="study-evidence-ref"
                >
                  ↗ {ev.filename || ev.title}{ev.page != null ? `, p. ${ev.page}` : ""}
                </a>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ReviewSession({ cards, index, flipped, onFlip, onRespond, answered, correct, complete, result, onDone }) {
  const card = cards[index];
  const progress = cards.length > 0 ? Math.round((index / cards.length) * 100) : 0;

  if (complete) {
    const pct = answered > 0 ? Math.round((correct / answered) * 100) : 0;
    return (
      <div className="study-review-view">
        <div className="study-session-complete">
          <span className="study-session-icon">✓</span>
          <h2>Session complete</h2>
          <p>You reviewed {answered} card{answered !== 1 ? "s" : ""}.</p>
          <div className="study-session-stats">
            <div className="study-stat">
              <strong>{correct}</strong>
              <span>Correct</span>
            </div>
            <div className="study-stat">
              <strong>{answered - correct}</strong>
              <span>Incorrect</span>
            </div>
            <div className="study-stat">
              <strong>{pct}%</strong>
              <span>Score</span>
            </div>
          </div>
          {result?.session_id && (
            <p className="study-session-saved">Session saved to your memory calendar.</p>
          )}
          <button className="primary-button" onClick={onDone} style={{ marginTop: 24 }}>
            Done
          </button>
        </div>
      </div>
    );
  }

  if (!card) return null;
  const typeLabel = { flashcard: "Flashcard", mcq: "Multiple choice", short_answer: "Short answer" }[card.card_type] || card.card_type;

  return (
    <div className="study-review-view">
      <div className="study-review-progress">
        <span>{index + 1} / {cards.length}</span>
        <div className="study-progress-bar">
          <div className="study-progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <span>{correct} correct</span>
      </div>

      <div className="study-flashcard-area">
        <div className={`study-flashcard ${flipped ? "flipped" : ""}`}>
          <div className="study-flashcard-front">
            <span className="study-card-type-label">{typeLabel}</span>
            <p>{card.front}</p>
            {!flipped && (
              <button className="primary-button study-flip-btn" onClick={onFlip}>
                Show answer
              </button>
            )}
          </div>

          {flipped && (
            <div className="study-flashcard-back">
              <span className="study-card-type-label eyebrow">Answer</span>
              <p>{card.back}</p>
              {card.explanation && <p className="study-card-explanation"><strong>Why:</strong> {card.explanation}</p>}
              {card.options && (
                <ul className="study-card-options review">
                  {card.options.map((opt, i) => (
                    <li key={i} className={opt === card.back ? "correct" : ""}>{opt}</li>
                  ))}
                </ul>
              )}
              {card.evidence?.length > 0 && (
                <div className="study-card-evidence review">
                  {card.evidence.map((ev, i) => (
                    <a
                      key={i}
                      href={`${API}/sources/${ev.source_id}/file`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="study-evidence-ref"
                    >
                      ↗ {ev.filename || ev.title}{ev.page != null ? `, p. ${ev.page}` : ""}
                    </a>
                  ))}
                </div>
              )}
              <div className="study-quality-row">
                <span className="study-quality-label">How well did you recall this?</span>
                <div className="study-quality-buttons">
                  {QUALITY_LABELS.map(({ value, label, hint }) => (
                    <button
                      key={value}
                      className={`study-quality-btn q${value}`}
                      onClick={() => onRespond(value)}
                      title={hint}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
