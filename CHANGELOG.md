# Changelog

## 0.1.0

- On-behalf-of flow from a client token to delegated Graph (`Files.Read.All`, `Sites.Read.All`), with app-only tokens rejected
- Microsoft Search source with optional KQL scope, and a local folder source with an access file for development and evals
- docx, PDF, markdown and text extraction with headings, tables and page numbers
- Paragraph-aware chunking with section tracking and overlap
- BM25 passage ranking fused with search rank, per-document cap
- Coverage gate that refuses without calling the model when nothing matches
- Claude and Azure OpenAI providers, streamed over server-sent events
- Citation validation in code, follow-up questions with history
- Feedback endpoint, redacted App Insights telemetry
- Evaluation runner with retrieval, permission-leak and answer checks
- Minimal browser client, CI with an eval gate, OIDC deployment workflow
