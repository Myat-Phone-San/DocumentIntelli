# Publish the chatbot

## Everyday conversation

The shared Python assistant replies politely to standalone English and Burmese
greetings, acknowledgements, thanks, and farewells without calling an LLM or
requiring uploaded documents. Examples include `mingalarpar`, `okay`, `ok`,
`thnank you`, `ကျေးဇူးပါ`, `ကျေးဇူးအများကြီးတင်တယ်နော်`, and morning,
afternoon, or evening greetings.

Other messages use the configured LLM to distinguish casual conversation from
document questions. Casual replies use recent conversation history and the user's
language. Substantive questions and ambiguous follow-ups continue through document
retrieval; mixed messages such as "hello, what are the rates?" are not fixed replies.
This routing adds an LLM request before retrieval for messages without a fixed
reply. Invalid routing output falls back to retrieval. Configure at least one
working LLM provider for generated casual replies.

Redeploy the Python backend after updating this code. No new environment variables
or frontend changes are required. Website, Telegram, and Messenger use this shared
behavior; the local RAG assistant also supports social replies.

## Optional Facebook Page Messenger: AI and human support

The Python API now supports Messenger alongside Telegram. It uses the same managed
knowledge base. Messenger customers cannot list, upload, or delete KB documents.
Human replies are written in Meta Business Suite Inbox; this is an application-level
AI pause, not an implementation of Meta Conversation Routing between multiple apps.
If other messaging apps are connected, configure their routing in Meta separately.

Add these variables to the **Python backend** service in Render, not the frontend:

```env
MESSENGER_ENABLED=true
MESSENGER_PAGE_ID=your_connected_facebook_page_id
MESSENGER_APP_ID=your_meta_app_id
MESSENGER_APP_SECRET=your_private_app_secret
MESSENGER_PAGE_ACCESS_TOKEN=your_private_page_access_token
MESSENGER_VERIFY_TOKEN=your_generated_private_verification_token
MESSENGER_GRAPH_VERSION=the_supported_version_from_your_meta_dashboard
MONGODB_URI=your_private_atlas_connection_uri
```

`MESSENGER_GRAPH_VERSION` must have the format `vXX.0`; choose an actual supported
version from your Meta app dashboard. Generate a verify token with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. Do not share secrets
or put them into frontend variables. Leave `MESSENGER_ENABLED=false` when unused.
No new Python dependencies are needed. Deploy one backend instance with one worker.

After deploying, configure Meta Messenger webhooks:

1. Callback URL: `https://YOUR-PYTHON-API.onrender.com/messenger/webhook`.
2. Verify token: the exact `MESSENGER_VERIFY_TOKEN` value set in Render.
3. Connect the Facebook Page and subscribe it to `messages`, `messaging_postbacks`,
   and `message_echoes` events. Verify the subscriptions actually include your Page.
4. Generate a Page token with the required `pages_messaging` access. Test using an
   account allowed by the app's current development/access settings. Complete the
   public-access permissions and reviews shown by Meta before opening to customers.
5. Send a greeting or a document question to the Page. Replies offer a
   **Talk to a person** quick reply. Selecting it pauses AI for that conversation
   and records a human-support request; it does not guarantee staff availability.

Configured Telegram admins can manage Messenger privately:

```text
/messenger
/messenger PAGE_SCOPED_USER_ID human
/messenger PAGE_SCOPED_USER_ID ai
```

The first command lists up to 50 conversations and their modes. The others pause
or resume AI. IDs are Messenger Page-scoped customer IDs from this list, not
Telegram IDs or the Page ID. A customer's `human` or `human support` message also
requests support. Customers cannot resume AI or access these admin controls.
Admins do not have to send a new reply when resuming: the next customer message
will use AI. `/messenger` is available only if the Telegram bot is also configured.
For HTTP administration, send `X-Admin-Key` to `GET /admin/messenger/threads` or
`POST /admin/messenger/threads/{psid}/mode` with `{"mode":"ai"}` or `{"mode":"human"}`.
These endpoints are intentionally not exposed by the public frontend proxy.

Before replying manually in Business Suite, pause AI with the admin command.
Receiving a Page message echo from outside this app also pauses AI automatically;
our own Send API echoes are ignored. This detection needs `message_echoes` and
correct `MESSENGER_APP_ID`. The worker rechecks mode before sending each AI reply
part, but an HTTP send already underway cannot be recalled. Explicitly pause first
to minimize overlap. Human replies are not added to the AI history; use a new,
self-contained question when returning to AI after a manual discussion.

Signed updates are saved to MongoDB before acknowledgement. The worker retries
failed work up to five times; inspect `messenger_inbox` records in state `failed`
and redacted backend logs if replies fail. Successfully handled updates expire
after seven days, but thread state and AI history remain until explicitly removed.
Delivery is at least once: a crash after Meta accepts a reply but before its progress
is saved can result in a duplicate reply. Publish privacy and deletion instructions
matching these stores before public use; this integration does not provide a public
automatic data-deletion callback or a human-staff notification system.

Automated sends use the standard 24-hour customer-contact window and do not use
the HUMAN_AGENT tag. Expired queued messages are skipped. Render Free cold starts
can delay delivery. Meta subscriptions, credentials, public access and real delivery
must be verified in your own account; repository tests do not contact Meta.

Reference: [Meta Messenger Send API and requirements](https://www.postman.com/meta/messenger-platform-api/documentation/iyp204x/messenger-platform-api).

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
