# Groundwork

Grounded answers over Microsoft 365 documents, served from Azure Functions, as the signed-in user.

Staff ask a question; Groundwork searches SharePoint and OneDrive with the user's own delegated access, reads and chunks the matching documents, and streams back an answer that cites the exact document and section. If the user's documents don't contain the answer, it says so instead of guessing.

```
client (SPA, Teams tab, anything with MSAL)
   │  Bearer token: api://<api-id>/access_as_user
   ▼
POST /api/ask ── validate token ── on-behalf-of ──► delegated Graph token
                                                     │
              Microsoft Search (as the user) ◄───────┘   only documents this user can open
                       │
              fetch → extract → chunk  (cache: item@eTag)
                       │
              BM25 over chunks ⨉ Search rank  → reciprocal rank fusion → top 6, max 3 per document
                       │
              coverage gate ── no match ──► refusal (model not called)
                       │
              model (Claude or Azure OpenAI), streamed
                       │
              citations checked in code ──► SSE: status · delta… · citations · done
```

## Why it is built this way

- **Permission trimming belongs to Microsoft 365.** Every search and download uses a delegated token obtained on-behalf-of the caller. There is no application permission on Graph, so there is nothing to filter in application code and nothing to get wrong.
- **No separate index.** Microsoft Search already indexes the tenant and respects permissions. Groundwork re-ranks passages from the top results instead of copying documents into a vector store that would need its own permission model and sync.
- **Citations are enforced, not requested.** Sources are numbered in the prompt; after streaming, cited numbers are checked against what was sent. Invented numbers are dropped and logged.
- **Refusal is cheap when it can be.** If no chunk covers enough of the question, the model is never called.

## Quick start (no tenant needed)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # 43 tests
python -m groundwork.eval                # retrieval eval on the sample policy corpus
```

Run the API locally against the sample corpus:

```bash
cp local.settings.sample.json local.settings.json    # dev mode, local corpus
# add ANTHROPIC_API_KEY to local.settings.json
func start
cd examples/web && python -m http.server 5173         # open http://localhost:5173
```

Dev mode (`GW_DEV_MODE=1`) skips sign-in and only works with `GW_SOURCE=local`; the app refuses to start if dev mode is combined with Graph. Send `x-dev-user: hr-admin` to see how the sample corpus restricts a document.

## API

`POST /api/ask`

```json
{ "question": "Who approves a 1,200 dollar expense claim?",
  "history": [ { "role": "user", "content": "..." }, { "role": "assistant", "content": "..." } ] }
```

Response is `text/event-stream`:

| event | data |
|---|---|
| `status` | `{"stage": "searching" \| "reading" \| "writing"}` |
| `delta` | `{"text": "..."}`, many |
| `citations` | `[{"id": 1, "title": "...", "url": "...", "section": "...", "page": 3}]` |
| `done` | `{"refused": false, "grounded": true, "request_id": "..."}` |
| `error` | `{"code": "throttled", "retry_after": "10"}` or `{"code": "internal_error"}` |

`401` with `WWW-Authenticate: Bearer error="interaction_required"` means the client must run interactive auth (consent or MFA) and retry.

`POST /api/feedback` `{"request_id", "helpful", "comment"}` records a rating against an answer. `GET /api/health` returns the version.

Browsers can't send headers with `EventSource`, so read the stream with `fetch`; `examples/web/index.html` shows how in about 40 lines.

## Configuration

| Setting | Default | |
|---|---|---|
| `GW_TENANT_ID`, `GW_API_CLIENT_ID` | | API app registration |
| `GW_API_CLIENT_SECRET` | | Key Vault reference in Azure |
| `GW_SEARCH_SCOPE` | | KQL added to every search, e.g. `path:"https://contoso.sharepoint.com/sites/HR"` |
| `GW_PROVIDER` | `anthropic` | or `azure_openai` (managed identity if no key) |
| `GW_MODEL` | `claude-sonnet-5-5` | model, or Azure OpenAI deployment name |
| `GW_TOP_K` | `6` | chunks sent to the model |
| `GW_MIN_COVERAGE` | `0.34` | share of question terms the best chunk must contain |
| `GW_CHUNK_CHARS`, `GW_CHUNK_OVERLAP` | `1200`, `200` | |
| `GW_HISTORY_TURNS` | `6` | prior messages kept for follow-ups |
| `GW_ASSISTANT_NAME`, `GW_REFUSAL_TEXT` | | per-deployment wording |

## Evaluation

`evals/questions.json` lists questions with the document that should answer each one, `null` for questions that must be refused, and `forbid` for documents that must never be retrieved for that user.

```bash
python -m groundwork.eval                           # retrieval: hit rate, permission leaks
python -m groundwork.eval --answers                 # also answers, citations, refusals (uses the model)
python -m groundwork.eval --corpus ./my-docs --questions ./my-questions.json --report out.json
```

CI runs the retrieval eval on every push and fails below 90% hit rate or on any leak. For a real tenant, export a sample of the document library to a folder, write 40 to 60 questions staff actually ask, and run the same command.

## Docs

- [Architecture and decisions](docs/architecture.md)
- [App registrations and permissions](docs/permissions.md)
- [Deploying to Azure](docs/deployment.md)

## Layout

```
function_app.py            HTTP routes, SSE framing, error mapping
groundwork/
  auth.py                  token validation, on-behalf-of
  sources/graph.py         Microsoft Search + download, delegated
  sources/local.py         folder source with optional access file, for dev and evals
  extract.py               docx, pdf, md, txt → text with headings and pages
  chunking.py              paragraph-aware chunks, section tracking, overlap
  ranking.py               BM25, coverage, reciprocal rank fusion
  cache.py                 item@eTag chunk cache
  prompt.py                system prompt, source rendering, citation checks, follow-up queries
  providers/               Claude, Azure OpenAI
  pipeline.py              retrieve → gate → stream → cite → log
  telemetry.py             redacted App Insights events
  eval.py                  evaluation runner
evals/                     sample corpus and questions
examples/web/              minimal browser client
```

## License

MIT
