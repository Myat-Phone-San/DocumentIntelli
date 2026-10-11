# Document Q&A Assistant

Cloud deployment and MongoDB history: see [DEPLOYMENT.md](DEPLOYMENT.md). Atlas stores conversations, messages, and approved KB chunks when `MONGODB_URI` is configured. Without it, conversation history defaults to local SQLite; XAMPP/MySQL remains available through `CHAT_DB_BACKEND=mysql`.

## New React / Next.js chat and Telegram bot

The new `frontend/` is a React application built with Next.js. It includes a responsive
chat interface, Markdown tables, copied answers, conversation search, server-persisted
history, and an authenticated administrator panel. It shares one FastAPI backend
with `telegram_bot.py`. Provider priority stays **Groq → OpenAI → Gemini**.

### Start the website

Run these commands in PowerShell from the project directory. The frontend requires
Node.js 20.9 or newer.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Keep that terminal running. In a second terminal:

```powershell
cd D:\Python\AIBankingIntelligenceRAG\frontend
npm install
npm run dev
```

Open **http://localhost:3000**. Select **Administrator** and enter the
`ADMIN_API_KEY` from your root `.env`. Upload a supported document and assign a
group such as Accounts, Cards, or Policies. Both the website and Telegram can then
answer from it. The key is held in browser memory only while the admin panel is open.
Conversation history is saved in the configured database; the browser identity and a display cache remain in localStorage. Delete a conversation to remove its stored messages.

A random administrator key was added to the local `.env` during setup if none existed.
For a fresh checkout, generate one and place it in `.env`:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Connect your Telegram bot

1. Open **@BotFather** in Telegram, send `/newbot`, and follow its steps.
2. Set `TELEGRAM_BOT_TOKEN` in the root `.env` to the token BotFather provides.
3. Set `ADMIN_API_KEY` to the same private key used by the backend.
4. Start the bot in a third terminal while the API remains running:

   ```powershell
   cd D:\Python\AIBankingIntelligenceRAG
   .\.venv\Scripts\python.exe telegram_bot.py
   ```

5. Message your bot privately with `/whoami`. Copy the numeric user ID into
   `TELEGRAM_ADMIN_IDS` in `.env`. Multiple administrators use comma-separated IDs.
   Stop and restart the bot after changing the IDs. With no IDs configured, admin
   access is disabled, but `/whoami` still works for setup.
6. Send `/start` again. Configured admins receive **Knowledge base**, **Upload
   document**, and **Help** buttons. Upload a document with a caption such as
   `Accounts` to select its group; the default is General. Use `/kb` to see grouped
   document buttons, document metadata, and confirmed deletion controls.

Regular Telegram users can ask questions and use `/new` to reset their conversation.
They do not receive admin buttons and cannot upload, list, inspect, or delete KB
documents. Every admin handler validates the numeric sender ID and requires a private
chat, including callbacks. Usernames and chat/group IDs do not grant admin access.
Public answers contain neutral reference labels rather than private source filenames
or a catalog endpoint; users necessarily receive relevant document facts in answers.
Only upload documents whose information is approved for your bot's audience. Chat
history is saved separately per Telegram user in the configured conversation database
and survives bot restarts. The bot uses polling, so it needs no public webhook for local operation.
BotFather has its own global command menu: do not publish admin-only commands there.

### Storage and operation

- The shared managed KB lives in `data/managed/knowledge.sqlite3`. It persists document
  groups and extracted chunks across restarts; source file bytes are processed in a
  temporary directory. Back up this database to retain the managed knowledge base.
- A local vector index is built from saved chunks when needed. The first indexing
  request may download the multilingual embedding model. A failed upload does not
  replace the existing index or save a partial document.
- Supported uploads are PDF, DOCX, XLSX, UTF-8 TXT, MD, and CSV, up to 20 MB each through Telegram (4 MB through the website).
  Scanned PDFs require OCR. Adding or removing documents rebuilds the index; this
  implementation is designed for a small knowledge base.
- Existing `data/raw`, CLI indexes, and Streamlit uploads are separate from the new
  managed KB. Upload approved files through the admin panel or bot to populate it.
- Run **one API worker**. The API owns the index; Telegram calls it over HTTP.
  The frontend proxies API requests so no provider keys are shipped to the browser.
- The defaults bind the website and backend to localhost. Public hosting needs HTTPS,
  request limits, and an access policy appropriate for your audience; the current web
  question endpoint is open to anyone who can reach it. Keep the API port private.
- Telegram connection requires your token; no live bot identity is created by this
  repository. Telegram sends retrieved excerpts to your configured LLM providers.
- Stop each process with Ctrl+C. Restart the API and bot after changing root `.env`.

Verification:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
npx playwright test
```

Browser checks use an installed Google Chrome. Backend tests use a fake vector index
and mocked LLM responses; they do not call paid APIs or Telegram. Browser tests mock
API responses while exercising the actual production interface.

Official integration references: [Next.js installation](https://nextjs.org/docs/app/getting-started/installation),
[Telegram Bot API](https://core.telegram.org/bots/api), and
[python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/).

The original Streamlit interface remains available below.

Upload documents on any topic in the Streamlit UI and ask questions based only on those files. Supported uploads: PDF, DOCX, XLSX, UTF-8 TXT, Markdown, and CSV. Multiple files are supported; scanned PDFs need OCR before upload. Legacy DOC/XLS files must be converted to DOCX/XLSX.

## Project structure

- `app.py`: Streamlit frontend
- `main.py`: CLI pipeline entry point
- `src/settings.py`: global configuration and constants
- `src/prompts.py`: prompt templates for the assistant
- `src/pipeline/`: ingestion, parsing, cleaning, chunking, embeddings, retrieval, and RAG logic
- `data/raw`: source documents
- `data/processed`: combined processed JSON and per-document extracted text files
- `data/chunks`: chunk metadata and text outputs
- `data/chroma`: Chroma persistent vector DB

## Features

- Document ingestion from `.docx`, `.pdf`, `.xlsx`, `.txt`, and related file formats
- Bank, category, and language metadata inference from filenames and content
- Text cleaning and chunking for retrieval
- Local embeddings with `sentence-transformers`
- Chroma DB vector search with metadata filters
- RAG-style answer generation with supporting source references
- Streamlit UI with multiple document uploads and a question form
- Session-specific in-memory upload indexes; changing files clears the previous answer

## Quick start

1. Create a Python environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a local `.env` file by copying `.env.example` and fill in your preferred provider keys.

4. Launch the UI (no knowledge-base build required):

   ```bash
   streamlit run app.py
   ```

5. Use **Upload documents**, select your files, then enter a question and click **Ask**. Remove all uploads to clear the session index. Uploaded files are parsed in a temporary directory and the temporary files are removed after extraction. Retrieved excerpts are sent to the configured LLM provider when generating answers.

The CLI `python main.py` remains available for the legacy banking corpus. The banking retrieval notes below describe that CLI workflow; the upload UI searches only the current uploaded files and does not apply bank filters.

## LLM configuration

This project supports three optional LLM providers:

- Groq (`GROQ_API_KEY`)
- Gemini (`GEMINI_API_KEY`)
- OpenAI (`OPENAI_API_KEY`)

Set `LLM_PROVIDER` in your `.env` file as a comma-separated priority list. The default priority is Groq first, OpenAI (ChatGPT) second, and Gemini third. The app will try providers in order and fall back automatically if a provider is unavailable or misconfigured.

Example `.env` values:

```env
LLM_PROVIDER=groq,openai,gemini
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=qwen/qwen3.8-27b
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini
```

If `LLM_PROVIDER=none`, the system works in retrieval-only mode and displays the retrieved sources without generating an AI answer.

If all providers fail, the answer area reports provider error types and HTTP status
codes instead of showing the internal prompt. Check model access as well as API keys;
an HTTP 404 may mean the configured model is unavailable to your account.

Groq partitions large comparisons into source-preserving requests and retries
HTTP 413 with smaller batches. It keeps every retrieved chunk, including later
rate schedules, and returns answers in sections when multiple requests are needed.
This can use more API calls. Character limits are estimates, not exact token counts;
a single oversized chunk or account limits can still require provider fallback.
HTTP 404 requires checking the configured model against those accessible with your
key. Restart Streamlit after changing `.env`.

For comparisons such as "Compare AYA and CB BANK Digital Banking Features", the
system detects the named banks and reserves retrieval results for each one. A
question naming one bank automatically scopes retrieval to that bank. Questions
without a bank name search the complete knowledge base.
Documents are split into chunks of at most 1,200
characters with up to 200 characters of overlap. Restart Streamlit after changing
configuration or chunking code to reload settings and rebuild its cached index.

Word ingestion reads paragraphs and tables in document order, preserving rate
tables next to their tenor and deposit-amount labels. Catalog entries are separated
at `Category:` boundaries before chunking; long entries repeat product/topic context.
Retrieval combines semantic candidates with BM25 keyword candidates using reciprocal
rank fusion. Keyword search scans the filtered local collection, which is suitable
for this small corpus but should move to a dedicated text index for larger datasets.
After extraction changes, run `python main.py` to refresh saved JSON and the vector
database, then restart Streamlit. Rates are evidence from local documents, not
verified current offers; conflicting table and paragraph values should be reported.
Each selected match also includes up to one neighboring chunk on either side from
the same source. Thus the displayed source count can
exceed the match limit (at most three times the limit per bank).

## Notes

- The system uses local files under `data/raw` and stores vector search results in `data/chroma`.
- `processed_documents.json` and readable source-by-source text files under
  `data/processed/extracted_text/<bank>/` are created after each pipeline run.
  Chunk data is saved to `data/chunks/chunks.json`.

## Windows startup troubleshooting

After updating dependencies, stop the running server with Ctrl+C and restart using
the project's interpreter so pip and Streamlit use the same environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

- Gemini uses the supported `google-genai` SDK. Restart the server after installing
  dependencies to stop loading the old `google.generativeai` integration.
- The Hugging Face unauthenticated-request warning is not a failed download. An
  optional `HF_TOKEN` in `.env` enables authenticated Hub requests. Keep it private.
- `ConnectionResetError: [WinError 10054]` in
  `_ProactorBasePipeTransport._call_connection_lost` reports a reset during Windows
  asyncio connection cleanup. That traceback alone does not identify the connection
  or establish that the server crashed. Open `http://localhost:8501` and try a query.
  If the UI fails or the error repeats, capture the surrounding server logs and
  whether it happens on page load, refresh, or question submission.
