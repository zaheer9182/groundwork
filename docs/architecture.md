# Architecture and decisions

## Request path

1. **Authenticate.** The bearer token is validated against the tenant's signing keys: signature, audience (`api://<id>` or the bare client id), issuer (v1 or v2 endpoint), tenant, expiry, and the delegated scope `access_as_user`. Tokens with `roles` and no `scp` (app-only) are rejected with 403.
2. **On-behalf-of.** MSAL's confidential client exchanges the user's token for a Graph token with `Files.Read.All` and `Sites.Read.All` as delegated scopes. MSAL caches it per user assertion, so a warm instance does one exchange per user per token lifetime. Consent or MFA problems come back as 401 `interaction_required` so the client can recover; anything else is 502.
3. **Search.** `POST /search/query` with `entityTypes: ["driveItem"]` runs as the user. Results are already trimmed to what they can open. An optional KQL scope (`GW_SEARCH_SCOPE`) narrows a deployment to particular sites or libraries.
4. **Read.** Up to 8 documents are downloaded in parallel and extracted (docx with heading styles and tables, PDF per page, markdown, text). A document that fails to download (locked, deleted, throttled) is skipped, not fatal.
5. **Chunk.** Paragraph-aware chunks of about 1,200 characters with 200 characters of overlap. Long paragraphs split at sentence ends. Each chunk carries its section heading and PDF page.
6. **Rank.** BM25 over the candidate chunks (title + section + text), fused with Microsoft Search's own document order using reciprocal rank fusion. At most 3 chunks per document so one long policy can't crowd out the rest. Top 6 go forward.
7. **Gate.** If the best chunk contains less than a third of the question's terms, the request is refused without calling the model.
8. **Answer.** The model gets a fixed system prompt (sources only, cite every sentence, exact refusal text, treat source text as data), prior turns for follow-ups, and the numbered sources. The answer streams to the client as it is generated.
9. **Cite.** After the stream ends, `[n]` markers are matched against the sources that were actually sent. Only those come back in the `citations` event, with the document link, section and page. An answer with no valid citation is marked `grounded: false`.
10. **Log.** One App Insights event per answer with a redacted question, a hashed user id, counts, coverage and latency.

## Decisions

**Microsoft Search instead of a vector index.** A separate index (AI Search, a vector store) needs its own copy of every document, a sync job, and a permission model that mirrors SharePoint's. Getting that last part wrong leaks documents. Using Microsoft Search with a delegated token means the tenant's own permissions apply on every request and there is nothing to keep in sync. The trade-off is that recall depends on Microsoft Search finding the right documents first; re-ranking at passage level recovers precision. If the eval shows recall is the weak point for a tenant, an index with security trimming can be added behind the same `Source` interface.

**The cache cannot leak.** Extracted chunks are cached by `driveId/itemId@eTag`. The cache is only ever read with keys taken from the current user's search results in the current request; there is no code path that searches the cache. A changed eTag means a new key, so edits are picked up on the next request. `tests/test_pipeline.py::test_warm_cache_does_not_leak_between_users` covers this.

**Coverage, not a score threshold, for refusal.** BM25 scores depend on how many candidates came back, so a fixed threshold refused valid questions when a tenant had only one matching document. Term coverage is independent of corpus size. It is only a "nothing here matches" filter: a question can share words with an unrelated passage (a wifi question matches the password policy), and that case is handled by the model's refusal rule and measured with `eval --answers`.

**Citations checked in code.** Models occasionally cite a source number that was never provided. Those are dropped from the response and counted in telemetry (`invented_citations`), which is a useful quality signal over time.

**Prompt injection from documents.** Source text is wrapped in `<source>` tags, any `</source>` inside a document is neutralised, attribute values are escaped, and the system prompt states that source text is never an instruction.

**Follow-ups.** A short follow-up ("what about contractors?") is a poor search query on its own, so it is prefixed with the previous user question for retrieval. The model receives the real history.

**One model call per question.** No query-rewriting call, no re-ranking call. That keeps latency to search + download + one streamed completion, and cost predictable.

## Scaling notes

- The chunk cache is per instance. With one always-ready instance it stays warm. When scale-out makes cold reads visible in latency, back `ChunkCache` with Blob Storage or Redis; the interface is two methods.
- Graph search and download are throttled per user and per app. 429s surface as a `throttled` error event with `Retry-After`.
- Large PDFs dominate latency. If a library is mostly long PDFs, pre-extracting on upload (a blob-triggered function writing to the shared cache) removes that cost from the request path.
