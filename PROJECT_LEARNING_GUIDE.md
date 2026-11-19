# Recall: A Deep Learning Project Learning Guide

This guide is a tour of the real Recall implementation in this repository. It is written as a
learning companion: read it from top to bottom once, then use the file map and experiments as a
reference while you explore the code.

Recall is best understood as a pipeline with a personality:

```text
chosen files / notes / webpages
        ↓
safe source registration + content hashing
        ↓
extraction: text, pages, slides, images, OCR
        ↓
document units + chunks + metadata
        ↓
SQLite + FTS5 + optional NumPy/FAISS vectors
        ↓
keyword / semantic / visual / hybrid retrieval
        ↓
Calendar, Connections, Study Mode, Ask My Memory
```

The important idea is that Recall does not treat an entire file as one giant blob. It creates
small traceable `document_units`, each connected back to a `source_id`, page or slide, extracted
text, thumbnail, metadata and optional vector references.

## 1. How to run the project

These are the two runtime commands after dependencies are already installed. Run them in separate
terminals from the appropriate project directories:

Backend:

```bash
PYTHONPATH=backend python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Frontend:

```bash
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. The backend API and OpenAPI documentation are available at:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`

Stop both processes with `Control+C`.

For a fresh environment, the project also contains [requirements.txt](./requirements.txt) and a
modern Python package definition in [pyproject.toml](./pyproject.toml). The frontend dependencies
are declared in [frontend/package.json](./frontend/package.json).

## 2. The stack, translated into plain language

### Python and FastAPI

Python is the backend language. FastAPI turns Python functions into HTTP endpoints. Pydantic models
validate JSON requests before application code receives them. Uvicorn is the ASGI server that runs
FastAPI.

### SQLite

SQLite is Recall's local database. It stores durable facts: sources, units, events, conversations,
entities, relationships, flashcards and provider cache entries. It is not the vector index.

The code explicitly enables foreign keys for every connection. This is essential because deleting a
source must cascade to units, evidence, events and other derived records correctly.

### FTS5

SQLite FTS5 is a full-text search extension. Recall stores the searchable text of every unit in the
`fts_units` virtual table. FTS5 provides BM25 ranking, tokenization, snippets and fast local
keyword search without sending a query to a cloud service.

### NumPy and optional FAISS

Recall uses the `FaissVectorStore` abstraction in [vector_store.py](./backend/app/retrieval/vector_store.py).

- If FAISS is installed, vectors are stored in `text.index` and `visual.index`.
- If FAISS is not installed, NumPy is used and vectors are stored in `text.npz` and `visual.npz`.
- The rest of the application does not need to know which backend is active.

The current laptop implementation normally uses the NumPy fallback. That is why you may not find a
FAISS database. Search the configured data directory for:

```text
text.index       # FAISS text store, when FAISS is installed
visual.index     # FAISS visual store, when FAISS is installed
text.npz         # NumPy text fallback
visual.npz       # NumPy visual fallback
recall.sqlite3   # SQLite metadata and extracted units
derived/
  thumbnails/    # rendered PDF/page/image previews
```

### React and Vite

The frontend is a React application bundled and served by Vite. Components hold view state and call
the backend through [frontend/src/api.js](./frontend/src/api.js). There is intentionally no second
frontend API layer or separate frontend database.

### Gemini

Gemini is behind provider interfaces. `ALLOW_CLOUD_AI=false` prevents cloud provider construction
and calls. When enabled, Gemini Embedding 2 provides 768-dimensional text/image vectors and the
Gemini Interactions API provides grounded generation for Ask My Memory.

## 3. Where the data actually lives

`DATA_DIR` controls the data directory. In this checkout, the existing installation may resolve a
relative `.recall-data` to the legacy `backend/.recall-data` location so an older launch does not
silently open a second empty database. The resolver is in [config.py](./backend/app/config.py).

Inspect the active location safely with:

```bash
PYTHONPATH=backend python -c "from app.config import get_settings; print(get_settings().data_dir)"
```

Never print `.env`; the command above prints only the data directory.

### 3.1 Sources are not chunks

The `sources` table represents original captured or mounted files. A source has:

- `source_id`: stable internal identity.
- `original_path`: the local file path, never returned directly by public result APIs.
- `source_type`: `pdf`, `docx`, `pptx`, `text`, `markdown`, `image`, or `webpage`.
- `capture_url`: original public webpage URL when applicable.
- `content_hash`: identity of the file contents.
- `indexing_status`: `pending`, `processing`, `done`, `partial`, or `error`.
- `excluded`: whether a missing/unavailable mounted file is excluded from retrieval.

### 3.2 Document units are the chunk/page layer

The canonical extracted material is in `document_units`:

- PDF pages become page units with `page_or_slide`.
- PPTX slides become slide units with `page_or_slide`.
- DOCX, TXT, Markdown and webpages become document units.
- Images become image units, optionally with OCR text.
- Long text units are divided into chunks by [chunker.py](./backend/app/ingestion/chunker.py).

Each unit stores `extracted_text`, `short_comment`, `tags_json`, `thumbnail_path`,
`text_embedding_ref` and `visual_embedding_ref`.

So the answer to “where are my chunks?” is: the canonical chunks are SQLite rows in
`document_units`. FTS5 has a searchable copy in `fts_units`; the vector files contain numeric
embeddings indexed by the same `unit_id` values.

### 3.3 Cache entries

`api_cache` stores provider responses keyed by provider, operation and content hash. This prevents
repeated Gemini calls for the same content/query combination and keeps the local-first behavior
predictable.

## 4. The complete ingestion journey

Follow one file through the code:

1. The frontend submits a folder through `POST /sources/folders` or a file through `POST /capture`.
2. [sources.py](./backend/app/api/sources.py) or [capture.py](./backend/app/api/capture.py) validates
   the request.
3. [scanner.py](./backend/app/ingestion/scanner.py) recursively finds supported files, skips hidden
   paths and unsafe symlinks, calculates SHA-256 hashes and registers sources.
4. `POST /index/run` calls `run_pending` in [indexer.py](./backend/app/ingestion/indexer.py).
5. `index_source` changes the source to `processing`, clears stale derived data and starts a new
   transaction.
6. [extractor.py](./backend/app/ingestion/extractor.py) produces `ExtractedUnit` objects.
7. `chunk_text` divides long text while preserving unit/page identity.
8. The metadata provider creates a comment and tags. If optional metadata fails, local heuristics
   are used and the source becomes `partial`, not falsely `done`.
9. A `document_units` row and matching FTS5 row are created.
10. The optional embedding provider creates text and/or visual vectors.
11. Vector IDs are the SQLite `unit_id` values, so retrieval can map a vector back to exact source
    and page data.
12. Thumbnails are written under `DATA_DIR/derived/thumbnails/<source_id>/`.
13. Entity discovery adds evidence-backed local objects and relationships.
14. A MemoryEvent records the import or modification.
15. The source status is committed as `done`, `partial`, or `error`.

One broken file is isolated. The rest of a folder can continue indexing.

### Status vocabulary

- `pending`: registered and waiting for indexing.
- `processing`: currently being extracted/indexed.
- `done`: configured operations completed.
- `partial`: core extracted text/search works, but optional OCR, metadata, embeddings or visual work
  is unavailable or failed.
- `error`: core extraction/indexing failed; retry is possible.
- `excluded`: the mounted original is unavailable and is excluded from search until it returns.

## 5. Backend file map

### Application and configuration

#### [main.py](./backend/app/main.py)

`create_app(settings)` builds the FastAPI application, installs CORS, initializes the database in
the lifespan hook and registers every router. The module-level `app` is what Uvicorn imports.

#### [config.py](./backend/app/config.py)

- `Settings`: immutable runtime configuration.
- `load_project_environment`: loads the project `.env` without overriding shell variables.
- `resolve_data_dir`: prevents relative-path launch differences from selecting the wrong database.
- `get_settings`: cached configuration factory.

Never put API keys in frontend code or logs.

### Database

#### [database.py](./backend/app/db/database.py)

Important responsibilities:

- `connect`: opens SQLite with row access and `PRAGMA foreign_keys = ON`.
- `get_connection`: context-managed connection/transaction helper.
- `initialize_database`: creates the schema and applies numbered migrations.
- schema migration functions: preserve old installations while hardening Connections tables.
- cache helpers: `cache_key`, `get_cached_response`, `put_cached_response`.
- `record_memory_event`: writes actual activity with a date basis.
- `_normalize_entity_title`: stable title normalization for entity identity.

Read the schema before writing SQL. The application uses foreign-key cascades heavily.

### Ingestion

#### [scanner.py](./backend/app/ingestion/scanner.py)

- `discover_files(folder)`: recursive supported-file discovery.
- `sha256_file(path)`: content identity.
- `sync_mount(connection, mount_id, folder, settings)`: register new/changed files and mark missing
  files excluded.
- `_remove_source_vectors`: deletes stale vector IDs from both stores.

#### [extractor.py](./backend/app/ingestion/extractor.py)

- `ExtractedUnit`: unit type, page/slide, text, optional image bytes and warning.
- `extract_file`: dispatch by source type.
- `_extract_pdf`: selectable text plus rendered page images and OCR fallback.
- `_extract_docx`: paragraph text extraction.
- `_extract_pptx`: slide text extraction.
- `_ocr_image`: invokes an existing local Tesseract executable when available.

OCR is deliberately optional. A missing OCR engine should create a visible partial warning rather
than inventing text.

#### [chunker.py](./backend/app/ingestion/chunker.py)

`chunk_text` produces bounded overlapping text chunks. Chunking is important because embedding and
RAG context windows should work with useful passages instead of entire large documents.

#### [indexer.py](./backend/app/ingestion/indexer.py)

- `index_source`: one-source lifecycle and durable status handling.
- `_index_source_in_transaction`: unit insertion, FTS, metadata, embeddings, thumbnails and
  discovery.
- `_prepare_extracted_units`: local OCR/Gemini image transcription boundary.
- `run_pending`: processes queued sources independently.
- `queue_reindex`: explicit reindex after provider/configuration changes.
- `_clear_source_derived_data`: stale unit/FTS/vector/thumbnail cleanup.
- `_record_source_event`: import/modify event deduplication.

### Retrieval

#### [fts_search.py](./backend/app/retrieval/fts_search.py)

- `sanitise_fts_query`: removes unsafe operators and conversational stopwords.
- `keyword_search`: FTS5 BM25 plus filename/title metadata matching.
- `_result`: serializes a safe result without exposing absolute paths.

#### [semantic_search.py](./backend/app/retrieval/semantic_search.py)

`vector_search` embeds the query, searches `text.index`/`text.npz` or the visual store, then joins
vector IDs back to SQLite units. Image-only semantic matches are conservatively filtered unless
their OCR text is readable and overlaps the query; explicit Visual mode is unaffected.

#### [visual_search.py](./backend/app/retrieval/visual_search.py)

Uses the shared text/image embedding space for explicit text-to-image retrieval.

#### [fusion.py](./backend/app/retrieval/fusion.py)

`reciprocal_rank_fusion` combines ranked result lists without assuming raw scores have identical
scales.

#### [reranker.py](./backend/app/retrieval/reranker.py)

The current lightweight fallback reranker boosts token overlap between the query and result title,
snippet, comment and tags.

#### [hybrid.py](./backend/app/retrieval/hybrid.py)

`retrieve_hybrid` selects keyword, semantic and explicit visual paths. The API requests source-level
grouping so a file appears once, while RAG keeps unit-level evidence for citation accuracy.

### AI providers

#### [providers.py](./backend/app/ai/providers.py)

- `GeminiProvider`: Gemini Embedding 2 text/image vectors plus optional metadata/vision extraction.
- `BGEProvider`: optional local text baseline; it does not provide image embeddings.
- `LocalMetadataProvider`: offline comments/tags heuristic.
- `build_embedding_provider`: provider construction with cloud gate.
- `build_metadata_provider`: cloud metadata or local fallback.
- `provider_status`: safe status payload without secrets.

Every provider operation is content-hash cached and validates vector dimensions/normalization.

#### [generation.py](./backend/app/ai/generation.py)

`GeminiGenerationProvider` uses the Interactions API with `store=false`. SDK errors are normalized
into safe provider messages; raw keys and document contents are not logged.

### Ask My Memory

#### [pipeline.py](./backend/app/rag/pipeline.py)

- `ask_question`: conversation persistence, retrieval, evidence verification and generation.
- `_verified_evidence`: checks every result’s `unit_id` belongs to the claimed `source_id`.
- `_build_context`: bounded numbered evidence blocks.
- `_grounded_prompt` and `_grounded_system_instruction`: enforce grounded JSON answers.
- `_parse_generation`: parses model output.
- `_remove_invalid_inline_citations`: strips unsupported citation numbers.

RAG uses unit-level retrieval even though the Search UI groups results by source. This is necessary
for exact page/unit citations.

#### [conversations.py](./backend/app/rag/conversations.py)

Stores conversations and messages locally. Follow-up context is assembled locally; Gemini does not
store conversation history by default.

#### [ask.py](./backend/app/api/ask.py)

Exposes `/ask`, conversation listing, message pagination and conversation deletion.

### Calendar, Connections, Study and Capture APIs

- [calendar.py](./backend/app/api/calendar.py): date grouping, On This Day and recap statistics.
- [connections.py](./backend/app/api/connections.py): entity browsing, evidence, relationships,
  review and safe deletion.
- [study.py](./backend/app/api/study.py): generation, due cards, SM-2 responses and sessions.
- [capture.py](./backend/app/api/capture.py): note, file and webpage capture validation.
- [sources.py](./backend/app/api/sources.py): mount, rescan, file serving, Finder reveal and source
  deletion.
- [status.py](./backend/app/api/status.py): indexing status, pause/resume and provider config.

## 6. Frontend file map

#### [App.jsx](./frontend/src/App.jsx)

The application shell owns active destination, calendar state, conversations, theme, sidebar
collapse and global Cmd+K handling. It refreshes source/indexing/calendar data after capture.

#### [api.js](./frontend/src/api.js)

The single service layer for requests. It handles JSON and multipart requests, capture, search,
calendar, conversations, Connections and Study Mode calls.

#### [Sidebar.jsx](./frontend/src/components/Sidebar.jsx)

Renders the fixed viewport navigation, expandable indexing details, conversation history and theme
control. The custom SVG mark is the Recall identity.

#### [SearchView.jsx](./frontend/src/components/SearchView.jsx)

Owns query/mode/date state, source-level result cards, readable snippet fallback, passage expansion,
web URL opening, Finder reveal and confirmed source deletion.

#### [ChatView.jsx](./frontend/src/components/ChatView.jsx)

Owns the conversational message thread and sends questions to `/ask`. It does not implement RAG;
the backend owns grounding and citation validation.

#### [EvidencePanel.jsx](./frontend/src/components/EvidencePanel.jsx)

Groups citations by source, shows one card per file, suppresses unreadable OCR previews and expands
additional cited passages.

#### [ConnectionsView.jsx](./frontend/src/components/ConnectionsView.jsx)

Browses entities, applies support filters, groups compatible normalized names in the display, opens
entity/source details, and handles relationship review/manual creation/deletion.

#### [StudyView.jsx](./frontend/src/components/StudyView.jsx)

Selects indexed sources/entities, renders generated cards, runs review sessions and submits SM-2
quality responses.

#### [CaptureMenu.jsx](./frontend/src/components/CaptureMenu.jsx)

Provides quick notes, public webpages, file uploads and mounted-folder capture with per-item status.

#### [styles.css](./frontend/src/styles.css)

Contains the warm editorial design system: ivory surfaces, charcoal text, terracotta accent, thin
borders, responsive layout, fixed sidebar behavior and reduced-motion support.

## 7. How to study the code effectively

### First pass: follow one TXT file

1. Start the backend and frontend.
2. Create a temporary note or mount a small folder.
3. Set a breakpoint or add temporary logging in `mount_folder`, `sync_mount` and `index_source`.
4. Inspect the source row in SQLite.
5. Inspect `document_units`, `fts_units`, and the vector store.
6. Search the exact phrase and then a filename-like query.
7. Delete the source and confirm each derived layer disappears.

Useful read-only SQLite inspection:

```bash
sqlite3 "$DATA_DIR/recall.sqlite3" "SELECT source_id,title,source_type,indexing_status FROM sources;"
sqlite3 "$DATA_DIR/recall.sqlite3" "SELECT unit_id,source_id,unit_type,page_or_slide,length(extracted_text) FROM document_units;"
```

Replace `$DATA_DIR` with the real configured directory; never run destructive SQL against the
personal database while learning.

### Second pass: understand retrieval

Compare these modes with the same query:

1. `keyword`: exact lexical and filename/source metadata.
2. `semantic`: text embeddings and cosine similarity.
3. `visual`: text-to-image retrieval in the shared multimodal space.
4. `hybrid`: keyword plus text semantic fusion.
5. `hybrid_rerank`: hybrid results reordered by the deterministic reranker.

Ask yourself: which component can explain this result? That question is the heart of a retrieval
system.

### Third pass: understand grounded generation

Trace `/ask` into `ask_question`:

1. Store the user message locally.
2. Retrieve units.
3. Verify each unit/source relationship.
4. Build numbered evidence.
5. Call the configured generator.
6. Reject invalid citation numbers.
7. Store the assistant message and citations.

The model is not trusted to invent source identity. The database-backed evidence list is the source
of truth.

## 8. Important design lessons

### A source is not a unit

A source is the original file. A unit is a page, slide, image or chunk. Search cards group units by
source for usability; RAG keeps units for citation precision.

### A vector is not a document

A vector only has meaning when its ID maps back to a unit. That is why vector deletion and stable
SQLite IDs are lifecycle-critical.

### `partial` is honest success

Text and FTS may work while OCR, metadata or cloud embeddings fail. The UI must tell the user what
is usable instead of flattening every outcome into “ready” or “failed”.

### Retrieval and generation are different

Search should be fast and evidence-oriented. Ask My Memory adds a generation layer after retrieval;
it should never replace ordinary search.

### Provenance beats impressive guesses

Connections, calendar activity and RAG citations are only useful when the user can inspect where the
claim came from.

## 9. Suggested learning projects

1. Add a test for a DOCX with two matching paragraphs and verify one source-level Search card.
2. Add a benchmark comparing FTS-only and semantic retrieval on a small synthetic corpus.
3. Trace a source deletion and draw the SQLite/vector/thumbnail cascade.
4. Add a local-only metadata provider and compare its tags with Gemini tags.
5. Extend the Connections UI with a “single-source entities” toggle without changing the database.
6. Add a test proving a webpage result opens its captured URL.
7. Measure indexing latency and vector file size before making performance claims.

## 10. What is intentionally not implemented

The current project does not silently promise browser extensions, screen recording, audio
transcription, cloud synchronization, a separate graph service, or a full source viewer with
highlighting. These are future possibilities, not hidden dependencies.
