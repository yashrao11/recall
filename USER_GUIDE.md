# Recall User Guide

This guide explains how to run and use Recall without assuming that you already know the codebase.
It is written for the local development version of the project.

## 1. What Recall is

Recall is a private, local-first memory workspace. You give it notes, supported files, public web
pages, or a folder to scan. Recall extracts useful text and page information, then makes that
material available in one place.

The seven capabilities work together:

1. **Natural Memory Search** finds indexed text with keyword search and, when an embedding provider
   is available, semantic, visual, or hybrid retrieval.
2. **Memory Calendar** shows real import, modification, capture, and study events by date.
3. **Visual Intelligence** searches indexed page images and screenshots when a multimodal embedding
   provider is enabled.
4. **Ask My Memory** answers questions from retrieved evidence and displays verified citations.
5. **Knowledge Connections** shows locally discovered entities, supporting evidence, and relationships.
6. **Study Mode** creates source-grounded cards and schedules reviews with SM-2. With cloud AI off,
   it uses a conservative extractive fallback from indexed text.
7. **Universal Capture** accepts quick notes, supported files, and public webpages through one capture
   menu.

Recall stores source metadata, extracted document units, search indexes, thumbnails, events,
relationships, conversations, study cards, and derived provider results. It does not replace your
original files, record your screen, read private messaging applications, or provide a browser
extension or audio transcription.

## 2. Architecture in simple terms

- **React frontend:** the screen you use in the browser.
- **FastAPI backend:** the local service that validates requests and runs Recall's operations.
- **SQLite:** the local database for sources, document units, events, conversations, entities,
  relationships, and study records.
- **Source files:** originals remain in their selected folder or in Recall's capture inbox.
- **Extraction:** PDFs, slides, documents, text, Markdown, webpages, and images are converted into
  searchable units.
- **FTS5 search index:** local keyword search over extracted text, comments, and tags.
- **Vector stores:** optional local NumPy/FAISS-compatible indexes for embeddings. FAISS is optional.
- **RAG pipeline:** retrieves evidence, builds a bounded context, asks the configured generator,
  and validates citations against the supplied units.
- **AI providers:** Gemini is optional and only used when `ALLOW_CLOUD_AI=true`; local metadata
  heuristics and keyword search remain available without it. BGE is an optional local text baseline.
- **Knowledge Connections:** stores entities, evidence, and relationships in the same SQLite database.
- **Study Mode:** reads the same document units and stores cards and review sessions locally.

Normal data flow:

```text
Capture or folder scan
        |
        v
Extract text/pages/images -> document_units + thumbnails
        |
        +-> FTS5 keyword index
        +-> optional text/visual vectors
        +-> local entity/evidence discovery
        +-> calendar memory event
        |
        +-> Search / Calendar / Connections / Ask My Memory / Study Mode
```

## 3. Prerequisites and installation

The current project requires:

- Python 3.11 or newer. The tested environment for this checkout is Python 3.13.
- Node.js and npm. The frontend uses Vite; Node.js 20 or newer is recommended.
- A terminal application.
- Optional document packages for PDF, DOCX, PPTX, and image processing.
- Optional `google-genai` only if you intentionally enable Gemini.

Open a terminal and move into the project directory:

```bash
cd "/Users/yashrao/Documents/YASH RAO/B.Tech/MY 7th BTech Sem/Deep Learning/Project/Recall"
```

Create or activate the Python environment from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the backend and development dependencies:

```bash
python -m pip install -e ".[dev,ingestion]"
```

This installs FastAPI, pytest, python-dotenv, multipart upload support, PyMuPDF, python-docx,
python-pptx, and Pillow. It does not download large AI models.

Install the frontend dependencies in a second terminal, or after the backend setup:

```bash
cd frontend
npm install
```

The repository contains [`.env.example`](./.env.example). To create a first configuration file,
run this once from the project root:

```bash
cp .env.example .env
```

The safe default is `ALLOW_CLOUD_AI=false`. Leave it that way for local-only use. If you later
choose to use Gemini, add your own key to `.env`, set `ALLOW_CLOUD_AI=true`, and restart the backend.
Never commit `.env` or paste its contents into a bug report.

The database is created automatically the first time the backend starts. It uses the configured
`DATA_DIR`; with the supplied example configuration and a root-directory launch, the default is
`.recall-data/recall.sqlite3`.

## 4. Starting and stopping Recall

### Start the backend

From the project root, activate the environment and start FastAPI:

```bash
source .venv/bin/activate
PYTHONPATH=backend python -m uvicorn app.main:app --reload --port 8000
```

Keep this terminal open. The backend API is at `http://127.0.0.1:8000` and its interactive API
documentation is at `http://127.0.0.1:8000/docs`.

### Start the frontend

In a second terminal:

```bash
cd "/Users/yashrao/Documents/YASH RAO/B.Tech/MY 7th BTech Sem/Deep Learning/Project/Recall/frontend"
npm run dev -- --host 127.0.0.1
```

Open the URL printed by Vite, normally `http://127.0.0.1:5173/`. If that port is busy, Vite may
choose `5174`; use the address it prints.

### Stop and restart

In each running terminal press `Control-C`. This stops the development server without deleting
your database or captured files. To restart, open the two terminals again, return to the same
directories, activate `.venv` for the backend, and run the two commands above.

Common startup problems:

- **`No module named app`:** run the backend command from the project root with `PYTHONPATH=backend`.
- **`Address already in use`:** stop the old server, or use another backend port and update the
  frontend API configuration if you intentionally change it.
- **Frontend cannot reach the API:** confirm the backend is running on port 8000 before refreshing.
  If Vite selected port 5174 instead of 5173, add `http://127.0.0.1:5174` and
  `http://localhost:5174` to your local `CORS_ORIGINS` value, or start the isolated backend with
  `CORS_ORIGINS=http://127.0.0.1:5174`, then restart the backend.
- **Missing document package:** install the optional ingestion group again with
  `python -m pip install -e ".[ingestion]"`.

## 5. Your first capture

1. Open the frontend in your browser.
2. Click **New content** in the left sidebar.
3. Choose **Quick note**.
4. Enter a title and a few complete sentences.
5. Click **Save to Recall**.
6. Wait for the per-item result. `Indexed` means the source entered the indexer; `partial` means
   the core content is available but an optional operation, such as embeddings, was unavailable.
7. Open **Search**, choose **keyword**, and search for a distinctive phrase from the note.
8. Open **Calendar** and select the capture date. A note capture is recorded with a capture date
   basis, not as proof that the note was studied.

Quick notes are stored as Markdown sources in the Recall capture inbox and use the same indexing
pipeline as other sources.

## 6. Natural Memory Search

Use Search when you remember words, a topic, or a visual description.

1. Choose **Search** in the sidebar.
2. Type a phrase such as `retrieval augmented generation`.
3. Choose a mode:
   - **keyword:** local FTS5 search; available without cloud AI.
   - **semantic:** embedding search when an embedding provider is operational.
   - **visual:** text-to-image retrieval when a compatible multimodal provider is operational.
   - **hybrid / hybrid rerank:** combines available retrieval lists.
4. Submit the search.
5. Select a result to open the protected source-file endpoint. Results show the filename and page
   where available.

With cloud AI disabled and no local BGE installation, semantic and visual modes accurately report
that their provider is unavailable; they do not pretend that keyword results are semantic results.

## 7. Memory Calendar

Use Calendar to revisit recorded activity rather than guessed activity.

1. Select **Calendar**.
2. Choose **Month**, **Week**, **3 Days**, or **Day**.
3. Use **Today** or the previous/next controls to move through time.
4. Switch between **Wall**, **Gallery**, **List**, and **Timeline**.
5. Click a memory card to open its source when the source is still available.
6. Use the right panel for the mini-calendar, On This Day history, and the actual recap statistics.

Events distinguish imported, modified, captured, and studied dates. A file's original modification
date is not presented as proof that you opened it.

## 8. Visual Intelligence

Visual search works on image-backed document units such as PDF pages and captured images when the
configured provider supports both text and image embeddings.

1. Capture or mount a supported PDF or image.
2. Let extraction complete.
3. Choose **Search** and select **visual** or **hybrid**.
4. Describe the visual content, for example `a flowchart showing authentication`.
5. Inspect the returned visual match, thumbnail, source title, and page reference.

Do not expect visual search to operate when the required provider is unavailable. Cloud-disabled
operation prevents document and query transmission to Gemini.

## 9. Ask My Memory

Ask My Memory is grounded question answering over indexed evidence.

1. Choose **Ask My Memory**.
2. Ask a question about your indexed files, such as `What did my notes say about backpropagation?`.
3. Read the answer and open the numbered evidence cards in the right panel.
4. Ask a follow-up in the same conversation if you want to refine the question.
5. Use **New conversation** to start a separate thread.

Citations are checked by the backend against real source and document-unit records. If retrieval
has no credible evidence, Recall returns a controlled insufficient-evidence response.

When `ALLOW_CLOUD_AI=false`, keyword retrieval and local storage still work, but Gemini generation
is not called. Ask My Memory therefore reports that generation is unavailable rather than inventing
an answer. Conversation records remain local.

## 10. Knowledge Connections

Connections are derived from actual indexed text and its metadata. They are not a claim that every
co-occurrence is a fact.

1. Choose **Connections**.
2. Browse the discovered entities, or search by title or alias.
3. Filter by entity type or status when useful.
4. Select an entity to inspect aliases, supporting evidence, incoming relationships, and outgoing
   relationships.
5. Select a source from **Explore source** to inspect entities and relationships supported by it.
6. Open an evidence source only through the displayed safe source action; absolute local paths are
   not exposed in the interface.
7. Use **Connect entities** to select two existing entities and a relationship type.
8. For eligible automatic relationships, use **Confirm** or **Reject**. Delete only relationships
   for which the backend permits deletion.

Automatic evidence is removed or reconciled when a source is reindexed. User-created entities and
relationships are treated as user curation and are not silently discarded merely because automatic
support changes.

## 11. Study Mode

Study Mode turns indexed source text into reviewable material.

1. Choose **Study Mode**.
2. Select a source, including a `partial` source when its extracted text is available, or select an
   indexed topic/entity.
3. Click **Generate study set**.
4. Expand a card to read its answer, explanation, and source/page evidence.
5. Click **Review all** or choose **Start review** for cards due today.
6. Reveal each answer and rate your recall from **Blackout** to **Perfect**.
7. Finish the session. The SM-2 schedule is saved, and the completed session becomes a calendar event.

With cloud AI disabled, Recall creates conservative local cloze cards from sufficiently substantial
indexed excerpts. Every local card points back to its source unit. Very short or weak text produces
an insufficient-evidence message instead of a fabricated question. With cloud AI enabled and a
working Gemini generation provider, richer flashcards, MCQs, and short answers may be generated.

## 12. Universal Capture

### Quick text

Use **New content → Quick note**, enter a title and text, then save. The note is persisted, indexed,
made searchable, and recorded on the calendar.

### Files

Use **New content → Upload files** and choose one or more files. The supported extensions are:

- `.pdf`
- `.pptx`
- `.docx`
- `.txt`
- `.md`
- `.jpg`, `.jpeg`, `.png`, `.webp`

Each file receives its own result. Unsupported, empty, oversized, or MIME-mismatched files are
reported individually. The upload limit is 25 MB per file. A failed indexing result is eligible for
the displayed retry action when the source was created successfully.

### Public webpages

Use **New content → Web link**, enter an `http` or `https` URL without credentials, and save. Recall
fetches only allowed public HTML or plain-text content with size and timeout limits, validates
redirects, rejects private/local network destinations, preserves the final public URL, and indexes
the extracted text. It does not access localhost, private IP ranges, or local network services.

### Folder mounting

Use **New content → Folder**, enter the absolute path of a folder you intentionally choose, and
click **Mount and index**. Recall preserves the existing mounted-folder workflow and indexes its
supported files. If you prefer the local API, replace the example path with a folder you intentionally
choose:

```bash
curl -X POST http://127.0.0.1:8000/sources/folders \
  -H 'Content-Type: application/json' \
  -d '{"path":"/absolute/path/to/your/folder","label":"My documents"}'
```

Then start a scan/index run through the API:

```bash
curl -X POST http://127.0.0.1:8000/index/run
```

The scanner skips hidden files and unsupported extensions, detects changed content by SHA-256 hash,
and marks files that disappeared from a mounted folder as unavailable/pending so their stale search
records do not remain searchable.

## 13. How information moves through Recall

```text
Capture -> received source -> extraction -> indexing -> Search
                                      |       |
                                      |       +-> Calendar event
                                      +-> Connections/evidence
                                               |
                         Ask My Memory and Study Mode retrieve from the same units
```

Capture success and indexing success are different. A file can be received but still be pending,
processing, partial, or failed. Extraction failures are errors. Extracted text with unavailable
embeddings is partial. Refresh the status shown in the sidebar or call `GET /index/status`.

If a source remains pending, confirm the backend is running and run `POST /index/run` for mounted
folders. If it is `error`, read the safe error message, correct the source or dependency, and use
retry where the capture interface offers it.

## 14. Privacy and cloud AI

By default, Recall is local-first:

- SQLite, FTS5, source metadata, capture files, thumbnails, conversations, entities, and study
  progress are stored under `DATA_DIR`.
- Local metadata heuristics and keyword search do not need a cloud request.
- `ALLOW_CLOUD_AI=false` prevents Gemini provider construction and cloud embedding, metadata, and
  generation operations.
- Optional local BGE can provide text embeddings if you deliberately install/configure it; it is not
  assumed to exist.
- `ALLOW_CLOUD_AI=true` permits Gemini only when the Gemini package, model configuration, and API key
  are valid. Captured document content, image bytes, and user queries may then be sent for the
  requested provider operation.

API responses omit API keys and absolute local source paths. Source-file and thumbnail endpoints are
restricted to files within enabled mounted folders and the derived-data directory. This application
does not currently provide user authentication; do not expose the development server to the public
internet.

## 15. Database, source files, and backups

With the example `.env` and a root-directory launch, the important local data is:

```text
.recall-data/
├── recall.sqlite3       # SQLite metadata, events, FTS data, conversations, study data
├── derived/             # thumbnails and vector/index artifacts
└── capture-inbox/       # originals submitted through Universal Capture
```

If `DATA_DIR` is set, use that directory instead. Original files from mounted folders remain in
their original folders; back up those folders separately.

Stop the backend before copying a backup so SQLite is not being written:

```bash
cp -a .recall-data/recall.sqlite3 ".recall-data/recall.sqlite3.backup-$(date +%Y%m%d-%H%M%S)"
cp -a .recall-data/derived ".recall-data/derived.backup-$(date +%Y%m%d-%H%M%S)"
cp -a .recall-data/capture-inbox ".recall-data/capture-inbox.backup-$(date +%Y%m%d-%H%M%S)"
```

Also back up every original mounted folder that Recall references. To restore, stop Recall first,
preserve the damaged directory as a separate copy, then restore the database, derived directory,
capture inbox, and original mounted files from matching backups. Restart Recall and verify Search,
Calendar, and source opening. Do not delete the database as a troubleshooting shortcut: it contains
events, conversations, entity relationships, study schedules, and provenance that cannot be rebuilt
perfectly from source files alone.

## 16. Troubleshooting

### Recall does not start

Confirm Python is activated, dependencies are installed, and the backend command is run from the
project root with `PYTHONPATH=backend`. The terminal error is the safest next clue.

### The frontend says it cannot reach the backend

Check that the backend terminal still shows a running Uvicorn process at port 8000. Refresh the
browser only after the backend responds at `http://127.0.0.1:8000/health`.

### No documents appear

New content must be saved through the capture menu, or a folder must be mounted through the API.
Unsupported extensions are skipped. Check the sidebar count and `GET /sources`.

### Upload fails

Check the extension, file size, MIME type, and that the file is not empty. A malformed PDF may be
accepted as a source and then show a retryable indexing error; replace it with a valid file.

### Indexing remains pending

Keep the backend running and use `POST /index/run` for mounted folders. Check `GET /index/status`.
Do not repeatedly re-upload the same content; capture uses content hashes for duplicate prevention.

### Search returns nothing

Try a distinctive keyword and `keyword` mode first. Confirm the source is indexed and not partial
because extraction failed. Semantic and visual modes need an operational provider.

### Study generation fails

Confirm the source has enough extracted text and is `done` or `partial`, then try again. Cloud-off
operation uses only the local extractive fallback and may correctly refuse very short text.

### Cloud AI is disabled

This is the safe default. Keyword search, local metadata, capture, Calendar, Connections, and local
extractive Study Mode remain available. Ask My Memory generation and Gemini semantic/visual operations
will report their unavailable state rather than making a request.

### A migration fails

Stop the backend, make a backup, and preserve the original database. Do not delete or manually edit
SQLite tables. Keep the migration error for diagnosis and restore the backup only after confirming
which copy is the original.

### Disk space becomes low

Stop Recall before moving or backing up data. Check the size of `DATA_DIR` and the original mounted
folders. Do not remove derived files or the database manually unless you understand that indexes and
provenance may become inconsistent.

### Recall closes unexpectedly

Restart from the terminal so the error remains visible. Make a backup before recovery work and
check the last indexing status. A failed source should not be treated as successfully indexed.

## 17. A simple daily routine

1. Capture a note, file, or public webpage with **New content**.
2. Wait for its result and note whether it is done or partial.
3. Search for the information using keyword mode first.
4. Open Calendar to revisit captured or modified material.
5. Inspect Connections when you want topics or relationships supported by evidence.
6. Use Ask My Memory when cloud generation is explicitly enabled and you want a cited answer.
7. Use Study Mode to generate local extractive cards or cloud-generated material and review due cards.
8. Back up the data directory and original source folders regularly.

## 18. Limitations and future possibilities

The current application does not implement:

- Browser extension capture.
- Audio transcription.
- Desktop/Tauri packaging.
- Cloud synchronization or multi-user accounts.
- Authentication and user permissions.
- A separate document viewer with page-level deep linking.
- Guaranteed semantic or visual search when optional providers are not installed or cloud AI is off.
- Offline generative RAG answers when cloud AI is off; Ask My Memory reports that generation is
  unavailable, while Study Mode has a deterministic extractive fallback.

These are limitations, not hidden promises. Recall should not be exposed to the public internet
without adding authentication and a threat-modelled deployment configuration.

## 19. Checklists

### First-time setup

- [ ] Python 3.11+ and Node.js are installed.
- [ ] `.venv` is created and activated.
- [ ] `python -m pip install -e ".[dev,ingestion]"` completed.
- [ ] `frontend/npm install` completed.
- [ ] `.env` was created from `.env.example` without committing secrets.
- [ ] Backend and frontend are running.
- [ ] The browser opens the Vite URL.

### First successful capture

- [ ] New content was opened.
- [ ] A non-empty note or supported file was submitted.
- [ ] The item result was read.
- [ ] Search keyword mode found the content.
- [ ] Calendar showed the capture/import event.

### Daily usage

- [ ] Capture new knowledge instead of leaving it in an unindexed folder.
- [ ] Check indexing status after larger captures.
- [ ] Search before asking a question.
- [ ] Inspect citations and evidence.
- [ ] Review due Study Mode cards.

### Backup

- [ ] Backend and frontend processes are stopped.
- [ ] `recall.sqlite3` is copied.
- [ ] `derived/` and `capture-inbox/` are copied.
- [ ] Original mounted folders are backed up.
- [ ] The backup location is separate from the working data directory.

### Safe shutdown

- [ ] Stop Uvicorn with `Control-C`.
- [ ] Stop Vite with `Control-C`.
- [ ] Do not delete `.recall-data`.
- [ ] Back up before moving or repairing data.

### Troubleshooting

- [ ] Check `/health`.
- [ ] Check `/index/status`.
- [ ] Check the source's indexing result.
- [ ] Try keyword search.
- [ ] Confirm the source file still exists.
- [ ] Preserve the database and error output before asking for help.
