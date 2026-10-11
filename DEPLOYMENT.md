# Publish the chatbot

Use **Vercel for the Next.js frontend**, **Render for the Python API and Telegram
webhook**, and **MongoDB Atlas Free for persistent data**. The files are prepared;
creating the accounts, entering credentials, and deploying are still required.

The default full Python stack includes a local embedding model and is a poor fit
for small free instances. `requirements-cloud.txt` and `render.yaml` use lightweight
BM25 keyword retrieval (`RAG_INDEX_BACKEND=lexical`) instead. Answers still use the
configured LLM providers, but retrieval does not provide semantic or cross-language
embedding matching. Use a larger always-on backend with `requirements.txt` and
`RAG_INDEX_BACKEND=hybrid` for the existing semantic + keyword search.

## 1. Create the free database

1. Create a MongoDB Atlas account and select a **Free** cluster, not a paid tier.
2. Create a database user with `readWrite` access to `banking_chat`. The Atlas login
   and database username are different credentials.
3. Add your local public IP to Atlas **Network Access** for local initialization.
   After creating the Render service, add its outbound IP ranges from its dashboard.
4. Choose **Connect → Drivers → Python** and copy the connection string. Replace
   the username and password; URL-encode reserved characters in credentials.
5. Set the root `.env` locally:

   ```env
   MONGODB_URI=mongodb+srv://USERNAME:PASSWORD@YOUR_CLUSTER.mongodb.net/?retryWrites=true&w=majority
   MONGODB_DATABASE=banking_chat
   RAG_INDEX_BACKEND=lexical
   ```

6. Install the driver and initialize the database from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-cloud.txt
   .\.venv\Scripts\python.exe scripts/init_mongodb.py
   ```

The script creates collections, JSON-schema validators, and indexes. It does not
insert sample conversations or remove existing data. It updates validators on
existing collections, so use a dedicated database for this app. Both new stores
also create their indexes when used.

`conversations` stores `_id`, `anonymous_id`, `title`, `created_at`, `updated_at`,
and `message_count`. `messages` stores `_id`, `conversation_id`, `sequence`, `role`,
`content`, `created_at`, and nullable `sources`, `retrieved`, and `follow_up`.
Successful user/assistant turns are committed together using an Atlas transaction.
The optional evidence fields currently remain null; private KB filenames and chunks
are not returned through the public history API. LLM request exceptions are not
stored as successful turns. MongoDB does not execute `schema.sql`.

`managed_documents` and `managed_chunks` preserve the admin knowledge base across
Render restarts. Upload approved documents again after switching from local SQLite;
existing SQLite documents and browser-only histories are not migrated automatically.
`telegram_inbox` stores pending webhook updates and expires completed updates after
seven days. `conversation_heads` records each owner's active conversation so `/new`
works even when records share the same millisecond timestamp. Failed Telegram sends
can be retried; crash recovery is at least once,
so a restart at an unfortunate moment can produce a duplicate reply.

Website identity is a random browser UUID used as a private bearer credential in
`X-Anonymous-Id`. It is stored in browser localStorage. Clearing it creates a new
identity; another browser does not automatically have access to your old history.
This is anonymous history, not a user account/login system. Telegram owners are
derived server-side from the Telegram user ID and the `ADMIN_API_KEY`. Keep that
key stable: rotating it changes the Telegram history identities. Users cannot read
or delete another owner's conversation by supplying its ID. `/new` creates a new
Telegram conversation while retaining the previous records.

Atlas Free has 512 MB for data and indexes and lacks automatic Atlas backups.
Export backups and monitor storage; message retention is not automatically limited.
See [Atlas limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/).

## 2. Push the code to GitHub

Create a GitHub repository and push this project using your IDE's Git interface.
The root `.env`, generated data, `.venv`, and frontend `node_modules` must stay out
of Git. Enter secrets in hosting dashboards instead of committing them.

## 3. Deploy the Python API to Render

Create a **Web Service** from the repository, or use Render **Blueprints** with
the included `render.yaml`. For a manual service:

| Setting | Value |
| --- | --- |
| Runtime | Python |
| Root directory | Repository root |
| Build command | `pip install -r requirements-cloud.txt` |
| Start command | `python -m uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1` |
| Plan | Free |
| Health check | `/health` |

Set these environment variables in Render:

```env
MONGODB_URI=your_private_atlas_connection_string
MONGODB_DATABASE=banking_chat
RAG_INDEX_BACKEND=lexical
ADMIN_API_KEY=your_existing_private_admin_key
LLM_PROVIDER=groq,openai,gemini
GROQ_API_KEY=your_groq_key
GROQ_MODEL=your_available_groq_model
OPENAI_API_KEY=your_optional_openai_key
OPENAI_MODEL=gpt-5-nano
GEMINI_API_KEY=your_optional_gemini_key
GEMINI_MODEL=gemini-2.5-flash
TELEGRAM_MODE=webhook
TELEGRAM_BOT_TOKEN=your_botfather_token
TELEGRAM_ADMIN_IDS=your_numeric_admin_id
TELEGRAM_WEBHOOK_SECRET=your_random_webhook_secret
BACKEND_URL=http://127.0.0.1:10000
```

Generate the webhook secret once with:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the resulting HTTPS backend URL, for example `https://YOUR-API.onrender.com`.
Check its `/health` endpoint. If not connecting Telegram yet, set
`TELEGRAM_MODE=polling`; the API then skips webhook startup and does not run a bot.
Run only **one worker and one backend instance** with this version.

Render Free sleeps after 15 minutes without inbound traffic and has an ephemeral
filesystem. Atlas preserves the data, while the index is rebuilt in memory after
wake-up. Cold starts delay the first response. The durable webhook inbox lets the
API acknowledge a Telegram update after saving it and process it in the backend.
Free hosting is suitable for a small demo, not a guarantee of always-on service.
See [Render Free](https://render.com/docs/free) and
[compute limits](https://render.com/docs/compute-plans).

## 4. Deploy the frontend to Vercel

1. Import the same GitHub repository into Vercel.
2. Set **Root Directory = `frontend`** and **Framework = Next.js**.
3. Add a server-side environment variable:

   ```env
   BACKEND_URL=https://YOUR-API.onrender.com
   ```

4. Deploy. Open the generated `https://YOUR-SITE.vercel.app` URL.
5. Open **Administrator**, enter your `ADMIN_API_KEY`, and upload approved documents.

Do not put MongoDB, Telegram, LLM, or admin credentials in `NEXT_PUBLIC_*` variables.
The Next.js proxy forwards only supported web endpoints; Telegram's internal routes
are not available through the website proxy.

Website uploads are limited to 4 MB to stay below Vercel's 4.5 MB function payload
limit including multipart overhead. Use the Telegram admin upload for files up to
20 MB. Vercel's Hobby plan is intended for personal, noncommercial use; check its
terms before publishing a business application. See
[Vercel function limits](https://vercel.com/docs/functions/limitations) and
[Hobby plan](https://vercel.com/docs/plans/hobby).

## 5. Register the Telegram webhook

Stop any locally running polling bot first. In the local root `.env`, set:

```env
PUBLIC_BACKEND_URL=https://YOUR-API.onrender.com
TELEGRAM_WEBHOOK_SECRET=the_same_secret_you_set_in_render
TELEGRAM_BOT_TOKEN=the_same_bot_token_you_set_in_render
```

Then explicitly run:

```powershell
.\.venv\Scripts\python.exe scripts/set_telegram_webhook.py
```

The script registers the HTTPS endpoint with Telegram and keeps pending updates.
Send `/start` privately to the bot. Admin IDs get KB buttons; other users can only
ask questions. Website and Telegram questions use the same Atlas-backed KB.
The backend validates Telegram's secret header before accepting an update.
See [Telegram webhook API](https://core.telegram.org/bots/api#setwebhook).

To return to local polling, explicitly remove the webhook with Telegram's
`deleteWebhook`, set `TELEGRAM_MODE=polling`, and start `telegram_bot.py` locally.
Do not run polling and webhook delivery for the same bot at the same time.

## Verify before sharing

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm ci
npm run build
npx playwright test
```

Tests use temporary SQLite and a mock MongoDB; they do not connect to your live
Atlas database or send Telegram messages. Verify one real conversation after
deployment, refresh the website, and inspect the records in Atlas Data Explorer.
Check another browser cannot read the first browser's history and that regular
Telegram users cannot open the KB controls.
