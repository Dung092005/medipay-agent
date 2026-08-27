# MediPay Agent — Final System Audit & Modernization Plan

> Audit refreshed: 2026-08-22 (UTC)
> Audited branch: `feat/data` at `d51c416`
> Baseline: `origin/develop` at `55a271b`; `feat/data` contains the baseline and is 20 commits ahead
> Scope: backend, GraphRAG runtime, Supabase, Qdrant, Neo4j, corpus pipeline,
> conversation memory, evaluation, observability, frontend, database
> restructuring, Docker/deployment, Render/Vercel, module boundaries, and every
> project/research artifact under `outsource/`.
>
> Planning rule: “cực đại” means the best measured quality/latency/cost Pareto
> point on a release-locked benchmark, not the largest model or the most complex
> graph. No external framework is adopted without an ablation win and an
> operational rollback path.

## Execution update — 2026-08-21

This plan is the design authority; the following items are now verified on
`feat/data` and against the live release, without changing `main`:

- **Release/data gate:** PostgreSQL, Qdrant and Neo4j were reached through the
  local `.env`; active release is `snapshot-c439751724ab7f10`. The active corpus
  has 682 documents, 37,170 chunks, 28,285 legal units and 14,393 semantic
  passages. Qdrant point IDs and `sha256(section_title + "\\n\\n" + text)`
  match all 14,393 semantic passages. The release manifest now records the
  collection, model, dimensions, count and parity contract. A fresh cross-store
  readiness check reports Qdrant 14,393 points and Neo4j 1,901 release nodes /
  187 approved serving relationships for the same release.
- **Security/admission:** `/chat` and `/analyze` require Firebase bearer auth;
  request history/body budgets and stable 413/429 errors are enforced. A
  Redis-backed sliding-window limiter is now available for horizontal Render
  scaling; local development falls back to the bounded in-memory limiter.
- **Runtime correctness:** legal-status metadata is fail-closed unless it has
  official provenance; complex temporal/relational questions no longer take a
  metadata early-return. Retrieval cache keys include release/model/collection
  and ranking configuration; low-risk answer cache entries additionally bind
  a context digest, while temporal/high-risk intents bypass it. Production
  startup validates required secret values and the Firebase service-account
  JSON shape before accepting traffic; LLM and embedding clients are process
  singletons.
- **Build/deploy:** backend Docker build uses an allowlisted non-root runtime
  and Render's injected `$PORT`; the build context excludes secrets, corpus,
  backups, `outsource/` and frontend artifacts. `render.yaml`, `web/vercel.json`
  and CI frontend/container/migration gates are versioned. These are deployment
  contracts, not proof that an external Render/Vercel project has been deployed;
  the current structural checks are recorded at
  `eval/results/platform-contract-current.json`. A local
  `managed-production` smoke now forces `APP_ENV=production` even when the
  developer `.env` says development and fails closed until
  `METRICS_TOKEN`, Firebase Admin credentials and explicit HTTPS CORS origins
  are supplied; this prevents accidental production-as-development startup.
  The redacted failure contract is recorded at
  `eval/results/managed-production-fail-closed-current.json`.
- **Dependencies:** `requirements/runtime.lock` pins the Python 3.11 runtime
  image; `requirements/migrate.lock` keeps the one-shot migration image
  minimal; pipeline and dev locks are isolated under `requirements/`.
- **Streaming/migrations:** `/api/v1/chat/stream` and the frontend SSE parser
  now emit stage progress plus a final answer only after guardrails/citations;
  raw provider tokens are intentionally withheld. `database/migrations/runner.py`
  adds ordered checksum validation, advisory locking and baseline/dry-run
  modes without running DDL in API startup.
- **Accuracy/concurrency:** high-risk responses receive a conservative claim →
  citation lexical audit and downgrade when the status/payment marker is absent
  from evidence. Retrieval now uses separate bounded DB phases around provider
  calls, and the context packer enforces both character and `tiktoken` budgets.
- **Verification:** backend tests (94), pipeline/corpus/graph/eval tests (73),
  Ruff for `src/` and `tests/`, compileall, Docker smoke/health, and Next
  lint/typecheck/build
  pass. Vendor tests under `outsource/` are intentionally not release tests.

Still open and explicitly not claimed complete: Firebase Admin credential
rotation/secret-manager installation, token-level SSE with claim verification,
managed shadow cutover/active-previous rollback and an authenticated
Render/Vercel staging smoke. Those require
external credentials/platform state or additional implementation and remain
tracked in the phased roadmap below.

### Execution update — continuation (2026-08-22 UTC)

The implementation pass continued on `feat/data` only. It did not modify or
push `main`.

- **Migrations and backup:** the sixteen ordered SQL migrations were applied to a
  disposable empty PostgreSQL volume, rerun idempotently, and exercised under
  two concurrent runner processes. The live conversation migration was applied
  after a PostgreSQL/Neo4j backup; the migration ledger is baselined and the
  active-release pointer/counts remain unchanged. Projection registry and review
  queue migrations were then applied idempotently. A compressed PostgreSQL
  backup (including registry rows), Neo4j graph export, manifest and checksums
  were written under an ignored backup directory. The restore tool was tested
  against disposable local PostgreSQL; the local Qdrant/Neo4j projection restore
  passed parity; local active/previous rollback now passes, while a full
  managed active/previous rollback drill remains open.
- **Conversation contract:** owner-scoped conversation/turn persistence now has
  UUID validation, RLS-safe queries, idempotent turn IDs, bounded retention,
  delete/list endpoints, and frontend UUID generation. Stable IDs are carried
  by normal and SSE chat requests; Langfuse grouping for stream events remains
  open.
- **Resilience:** provider timeout budgets, bounded concurrency, embedding
  single-flight and an asynchronous circuit breaker are implemented and tested.
  Chunked request bodies now enforce the same 413 limit as Content-Length
  requests. Local Compose includes an internal Redis limiter service.
- **Containers:** the corpus-worker image now has a valid Uvicorn entrypoint,
  `/app/database/pipeline` import boundary, hashed pipeline lock and a non-root
  smoke check. Local Compose was rebuilt from current sources; PostgreSQL,
  Qdrant, Neo4j, Redis, API and web containers start with healthchecks. Local
  Qdrant/Neo4j projections were restored from the release backup and local API
  readiness is now `200 ready`; liveness and web HTTP smoke pass.
- **Verification:** backend suite is 91 passing tests and pipeline/corpus/eval
  suites are 73 passing tests (30 upstream Neo4j deprecation warnings only);
  Ruff, compileall and `git diff --check` pass;
  frontend lint/typecheck/build and `npm audit` pass; runtime, migrate, web and
  pipeline images build with locked dependencies. Full current human quality,
  load/p95, external staging and credential rotation remain open.
- **Live parity after registry migration:**
  `eval/results/live-parity-projection.json` is `pass`: PostgreSQL 682 documents/
  37,170 passages, Qdrant 14,393 semantic vectors, Neo4j 1,901 release nodes,
  187 approved-evidence edges, zero identity/content mismatches. Runtime
  readiness returned database/Qdrant/Neo4j/LLM/embedding all true. A fresh
  managed parity run at `eval/results/live-corpus-parity-current.json` repeats
  the same counts and zero identity/content/relationship mismatches; the
  14,393 PostgreSQL embeddings are intentionally externalized to the verified
  Qdrant-ready artifact, not missing data.
- **Current retrieval/eval evidence:** the restored local Qdrant semantic
  benchmark passed its release gate (80 cases, Recall@20 88.75%, ANN document
  overlap 100%) at `eval/results/local-qdrant-semantic-restored.json`. A fresh
  live 36-case run completed with 36/36 agent answers and 30/30 RAGAS metrics
  observable, without LangSmith 403s; the historical deterministic baseline was
  11/36 and the earlier post-hardening rerun is retained at
  `eval/results/run-20260822-live-ragas-final/` with 15/36 case gates. The
  latest completion audit reaches 36/36 gates with zero fallback/metric errors
  and source means factual correctness 0.982, response relevancy 0.763,
  faithfulness 1.000 and quality 0.961 at
  `eval/results/run-20260822-completion-audit-v3/`. All document lookup,
  policy-date, category and six deterministic safety/policy cases pass. The
  earlier concise-metadata ablation at
  `eval/results/run-20260822-live-ragas-optimized/` remains rejected because it
  collapsed response relevancy to 0.310.
- **Current local load evidence:**
  `eval/results/local-readiness-load-prewarm-qdrant119-warm.json` records
  100/100 HTTP 200 readiness responses (concurrent-burst p95 316.44 ms after
  startup prewarm; sequential warm probes are ~15–17 ms); the historical
  restart report remains at `eval/results/local-readiness-load-final.json`, while
  `eval/results/local-parity-projection.json` proves PostgreSQL/Qdrant/Neo4j
  count and identity parity after restoring the local projections.
- **Current managed read-only load/eval evidence:**
  `eval/results/live-release-292-plus-semantic-current.json` passes the release
  gate with exact identifier 100/100, graph evidence 100/100 and semantic
  Recall@20 88.75% (80 cases); `eval/results/live-readiness-load-100-current-v2.json`
  returns 100/100 HTTP 200 readiness responses (p95 403.30 ms), while the
  provider-backed 10-request smoke in
  `eval/results/live-chat-provider-load-current-v2.json` completes 10/10 with
  p50 6.83 s and p95 10.81 s.
- **Metadata quality hardening:** exact title/status/date/category answers now
  render only the fields requested by the user while retaining a complete
  provenance quote in the citation. The latest read-only 36-case run reached
  36/36 case gates, with 100% document lookup and policy-date passes, zero
  fallback/metric errors, factual correctness mean 0.982, response-relevancy
  mean 0.763, faithfulness mean 1.000 and quality mean 0.961 in
  `eval/results/run-20260822-completion-audit-v3/`; all 10 category cases pass
  the 0.60 relevancy threshold.
- **Claim verifier:** the guardrail now rejects claims whose concrete numbers or
  legal-status polarity conflict with cited evidence, in addition to lexical
  overlap/provenance checks. Regression coverage is in
  `tests/test_agents/test_graph.py`.
- **Evaluation/monitoring hardening:** the isolated RAGAS evaluator explicitly
  disables LangSmith tracing/export before importing LangChain, preventing
  ambient credentials and 403 trace uploads. Repository-owned Prometheus alert
  rules and ownership/runbook requirements are now in
  `ops/monitoring/prometheus-alerts.yml` and `ops/monitoring/README.md`.
- **Bounded provider load:** a 10-request semantic GraphRAG run at concurrency 4
  completed 10/10 with no provider error (`eval/results/local-chat-provider-load.json`);
  p50 was 6.84s and p95 11.62s, so the local full-answer latency gate remains
  open even though readiness and deterministic routes are fast.
- **Shadow/data contracts:** the eight additive migrations are now applied to the
  live `.env` database after a release backup. Live shadow rehearsal passes
  document/legal-unit/chunk counts and hashes; the SAT projection indexes
  90,438/90,438 cells; normalized document signatures and explicit runtime
  grants are migration-owned. Live parity remains pass after the change. Evidence
  is in `eval/results/live-shadow-rehearsal.json`,
  `eval/results/live-table-cell-sat.json`,
  `eval/results/live-parity-projection-after-shadow.json`, and
  `eval/results/live-conversation-rls.json`.
- **Pointer contract:** read-only active-release checks pass locally and live at
  `eval/results/local-release-pointer.json` and
  `eval/results/live-release-pointer.json`; active pointer, legacy state and
  three projection fingerprints/counts agree. The managed/live
  `rollback_ready=false` remains honest because no second physical
  Qdrant/Neo4j/PostgreSQL release is retained there; the local-full drill now
  has a retained previous release and is verified separately below.
- **Local physical rollback rehearsal:** a second PostgreSQL release, Qdrant
  collection and Neo4j graph were cloned, the stable alias/pointer cut over,
  readiness and exact/semantic retrieval verified, then rolled back. The
  candidate remains retained as `previous` locally; evidence is in
  `eval/results/local-release-rollback-drill.json` and
  `eval/results/local-release-rollback-drill-verify.json` (generation 11,
  14,393/14,393 Qdrant points and 1,901/187 Neo4j nodes/approved edges at both
  cutover and rollback; both physical Qdrant collections are green). Managed production
  still has only one physical Supabase/Qdrant release and remains open.
- **Additional contracts:** `20260830_conversation_anchors` persists a bounded
  typed anchor array per turn; `20260831`–`20260833` add guarded
  active/previous generation bookkeeping and an advisory-locked
  `ops.activate_release()` parity gate. Legal-unit enumeration now uses a
  deterministic extractive formatter, and explicit conjunctions use one
  bounded embedding/Qdrant batch before the normal per-subquery verification
  path.
- **Table ontology extension:** `20260832_table_cell_provenance` now attaches
  each SAT fact to its canonical `document_id` (369 documents covered in the
  active release) and optional `legal_unit_id`; live/local parity remains
  90,438/90,438. The current extractor has no legal-unit anchors in these
  table payloads, so semantic row-subject enrichment remains explicitly open.
- **Latest verification:** the fresh migration rehearsal now applies all 16
  ordered migrations and reruns with zero applies (`eval/results/migration-
  rehearsal-current.json`). Live dry-run skips all 16. Local API `/ready` and
  `/health`, web HTTP smoke, and the structural deploy contract pass after the
  latest image rebuild; SBOM/CVE reports were regenerated for the current API
  image.
- **Final local verification:** the adversarial suite is now 9/9 deterministic
  checks pass, including 100-item retrieval flooding and memory-hint scale
  cases (`eval/results/local-adversarial-suite.json`), the active Qdrant
  collection is green with 14,393/14,393 indexed vectors and all 8 sampled
  evidence hashes verified (`eval/results/local-qdrant119-active-index.json`),
  and the local pointer reports `rollback_ready=true` with a retained previous
  release. These are local safety/performance gates, not managed production
  approval.
- **Observability hardening:** the API now emits bounded Prometheus-compatible
  `/metrics` counters and latency summaries for HTTP, retrieval and generation;
  production requires `METRICS_TOKEN`, while local smoke confirms the endpoint
  and its labels contain no user/query/document identifiers at
  `eval/results/local-metrics-smoke.json`. Provider queue/in-flight counters
  are covered by unit tests and appear after a real provider request.
- **Image hardening:** API, migration and pipeline runtime images now use the
  non-root `gcr.io/distroless/python3-debian12` runtime; the Next runner uses
  the non-root Chainguard Node runtime. Packaging tools (pip/setuptools/wheel)
  and npm are absent from the final layers, shell-based healthchecks were
  replaced with JSON-form checks, and rebuilt services passed local
  health/ready/web smoke. Current Docker Scout high/critical SARIF reports are
  empty for API/web/migrate/pipeline (`eval/results/cves-*-high-critical.sarif`),
  so the structural deploy contract now reports `security_gate_pass=true` for
  all four runtime images. The runtime lock now uses
  `langchain-openai==1.6.0` with `openai==2.54.0`; current Scout full SARIF
  reports no vulnerable package, and a real container provider smoke returned
  an `AIMessage` with LangSmith tracing disabled. External platform attestation
  remains open.
- **Provenance hardening:** semantic hydration now rejects missing or mismatched
  Qdrant/PostgreSQL embedding-input digests, and ordinary passage evidence must
  carry a matching content hash. Deterministic red-team coverage now includes
  missing-hash, mismatched-hash and bounded PageIndex cases.
- **Local secret hygiene:** the live `.env` was used only through redacted
  presence checks and the three managed database clients; its mode is now
  `0600`. No Firebase Admin JSON/private key was copied into the repository or
  examples; the pasted credential must still be revoked/rotated externally.
- **Retrieval/conversation hardening:** held-out diversity ablation keeps
  Recall@20 at 88.75% while reducing duplicate ratio 31.31% → 26.19%; bounded
  conversation reference resolution and local RLS isolation pass. Evidence is
  in `eval/results/local-retrieval-ablation.json` and
  `eval/results/local-conversation-rls.json`.
- **Release-locked retrieval coverage:** exact identifier 100/100 and graph
  evidence 100/100 pass; the separate 80-case thematic semantic benchmark
  passes Recall@20 88.75%. The evaluator no longer applies semantic scoring to
  exact-signature IDs; combined report is
  `eval/results/local-release-292-plus-semantic.json`. Policy/no-answer/table
  cases now have a 12/12 deterministic agent-level edge-case pass at
  `eval/results/release-edge-cases.json`; human adjudication and full 292-case
  RAGAS remain open.
- **Image security evidence:** CycloneDX SBOMs and high/critical CVE SARIF
  reports are generated for API/web/migrate. The current structural deploy contract
  passes with `security_gate_pass=true` and no vulnerable package in the rebuilt
  API image; API/web/migrate high-critical SARIF reports are empty. External
  Render/Vercel attestation still requires follow-up before production promotion.
- **Safety/evaluator hardening:** ambient LangSmith tracing/API variables are
  disabled before LangGraph import and in the Langfuse adapter; deterministic
  policy routes now explicitly cover authorization, secret/prompt refusal,
  missing coverage inputs and medical-safety redirection. The policy evaluator
  now reads both legacy `required_facts` and policy `gold_facts`, normalizes
  Vietnamese diacritics, and has regression coverage. Exact metadata lookup
  accepts year-qualified and abbreviated signatures (for example
  `11/CT.UBND`) while keeping relational/temporal questions on full retrieval.
- **Projection control plane:** migration `20260823_release_projections` now
  records immutable PostgreSQL/Qdrant/Neo4j locators, release fingerprints and
  expected/actual counts. The live parity verifier passes with all three rows
  `ready`; runtime readiness rejects missing or count-mismatched projection
  rows. A new backup includes the three registry rows and compressed restore
  artifact metadata.
- **Local projection restore:** the Docker target was rebuilt from the release
  artifact and Neo4j export; `eval/results/local-parity-projection.json` passes
  with PostgreSQL 37,170 passages, Qdrant 14,393 points and Neo4j 1,901 nodes /
  187 approved edges. Readiness compares Neo4j provider counts to registry
  metadata instead of treating connectivity as sufficient.
- **Application/product hardening:** ingest no longer executes DDL; it fails
  closed unless migration-owned tables exist. Public `chat_history` is removed
  from the request contract and frontend payload. Cost quota is enforced in
  addition to request rate limiting. Admin review decisions persist through the
  RLS-protected review queue API. Claims now carry typed category, subject,
  source span/hash and verification fields; application ports/use-case adapters
  isolate the API from direct LangGraph invocation.

### Execution update — workspace cleanup (2026-08-22 UTC)

- Dependency inputs are now grouped under `requirements/`: runtime, migration,
  pipeline and dev sets each have a purpose and a hash-pinned lock. The old
  duplicate root requirement files and duplicate environment examples are
  removed; `make setup` is the supported entrypoint.
- PostgreSQL authority is physically grouped under `database/postgres/`
  (`schema.sql` and ordered migrations). Qdrant has an explicit projection
  contract under `database/qdrant/`; Neo4j and Firebase remain isolated. The
  root database README plus Dev/AI guides document ownership and boundaries.
- `data/`, `ops/`, `packages/`, `src/` and `tests/` now each have separate
  Dev/AI guidance. Generated data, backups, caches, evaluation output and
  `outsource/` remain outside the release source allowlist and are not staged.
- Root `docker-compose.yml` is the sole local service orchestrator. The API,
  migration, corpus-worker and web images remain separate because they have
  different dependency and privilege boundaries; the duplicate standalone
  Neo4j compose file was removed.

## 1. Kết luận cuối cùng

Hệ thống đã vượt qua giai đoạn “demo RAG nối vector search”. Kiến trúc dữ liệu
và retrieval hiện đủ tốt để tiếp tục làm MVP BHYT/viện phí có kiểm soát:

- Supabase là canonical source cho document, legal unit, span và lexical index.
- Qdrant alias `medical_legal_active` đang phục vụ semantic retrieval theo release.
- Neo4j chỉ làm navigation, và runtime chỉ đọc cạnh `approved_evidence`.
- Exact metadata route, lexical, semantic, PageIndex/legal-unit expansion,
  graph re-retrieval, provenance check và citation đã online.
- Case Quảng Ngãi đã truy xuất đủ 8 mục a–h, trả lời đầy đủ và không lộ ID nội bộ.

Tuy nhiên hệ thống **chưa production-ready**. Năm rủi ro lớn nhất hiện tại là:

1. Chưa có current end-to-end gold evaluation đủ rộng sau các bản sửa mới.
2. Latency full answer khoảng 12 giây; generation là bottleneck ổn định, còn
   Qdrant cold/outlier là bottleneck p95.
3. Firebase login/profile đã có; `/chat` và `/analyze` hiện bắt buộc bearer
   token, frontend gửi ID token, có body/history budget và process-local rate
   limit; distributed Redis quota đã được implement nhưng chưa smoke-test trên
   Render thật. Vẫn thiếu credential rotation và external admin browser rehearsal.
4. Data layer đang tồn tại đồng thời schema/pgvector legacy và Qdrant runtime;
   Docker build context cũng có nguy cơ mang theo vài GB artifact local.
5. Chưa sẵn sàng deploy Render/Vercel thật: Blueprint/project contracts và
   `$PORT` runtime đã có, nhưng chưa có external staging smoke, secret
   installation/rotation và production approval.

Không nên full rewrite sang DDD hoặc microservices. Nên chuyển dần sang
**modular monolith + tactical DDD + ports/adapters**, giữ nguyên API và database
contract đang hoạt động. Database nên migrate bằng shadow schema và parity
cutover; không `DROP`/normalize trực tiếp active tables và không re-embed chỉ vì
đổi relational schema.

## 2. Mức hoàn thiện hiện tại

| Hạng mục | Mức ước lượng | Nhận định |
|---|---:|---|
| Corpus/release foundation | 85–90% | Mạnh nhất dự án; deterministic, có hash và provenance |
| Supabase canonical/lexical/PageIndex | 80–85% | Đủ cho MVP; chưa đủ toàn bộ pháp luật y tế |
| Qdrant semantic serving | 80% | Online, parity tốt; recall và cold latency còn phải tune |
| Neo4j serving graph | 65–70% | Đúng vai trò navigation; chỉ 187 cạnh đủ chuẩn serving |
| Online retrieval runtime | 70–75% | Exact/hybrid/PageIndex/graph đã online; planner/reranker còn heuristic |
| Answer/citation reliability | 70–75% | Provenance/claim guard và metadata citation đã có; human precision gate và full claim ontology còn mở |
| Eval/observability | 55–60% | Có deterministic gates và Langfuse; current RAGAS đã chạy lại nhưng quality gate còn fail |
| Backend API | 75–80% | Auth/admission/SSE/error contract/readiness đã có; distributed quota và external auth smoke còn mở |
| Frontend người dùng | 70–75% | Chat/login/SSE/cancel/token propagation chạy qua API thật; còn browser staging smoke |
| Admin/product workflows | 60–65% | Admin auth/review API, RLS và audit đã là dữ liệu thật; browser rehearsal và correction workflow còn mở |
| Production readiness tổng thể | 40–45% | Có thể pilot có giám sát, chưa nên public production |

## 3. Trạng thái dữ liệu và ba database

### 3.1 Supabase

Active release: `snapshot-c439751724ab7f10`.

Live read-only inventory chụp ngày 2026-08-19 và đối chiếu lại ngày 2026-08-21:

| Chỉ số | Giá trị |
|---|---:|
| Tổng PostgreSQL database | 170.265.747 bytes (~162,4 MiB) |
| Releases đang lưu | 1 active, 0 staging, 0 superseded |
| Canonical documents | 682 |
| Documents có content | 682 |
| Answer-ready documents | 429 (62,9%) |
| Index-eligible documents | 596–597 tùy stage manifest |
| Legal units | 28.285 |
| Lexical passages/chunks | 37.170 |
| Semantic passages | 14.393 |
| Table-row passages | 12.429 |
| Alias documents | 8 |
| Document tables / cells | 1.328 / 90.438 |
| pgvector còn trong Supabase | extension + cột schema, 0 vector rows |

Phân bố storage lớn nhất:

| Table | Total size |
|---|---:|
| `chunks` | 73.818.112 bytes |
| `table_cells` | 37.289.984 bytes |
| `legal_units` | 27.934.720 bytes |
| `documents` | 17.776.640 bytes |

Điểm tốt:

- 0 content mismatch trong live parity.
- 0 missing semantic passage trong canonical validation.
- Text canonical được hydrate lại từ Supabase; Qdrant không phải nguồn text.
- Legal units có source span để tạo citation.

Điểm chưa đủ:

- 253/682 documents chưa answer-ready.
- Corpus chỉ đủ cho phạm vi BHYT/viện phí hiện có, không đại diện toàn bộ luật y tế.
- `datasets.manifest` của active release đang trả `semantic_passages=0` qua
  `current_dataset_release()`, dù Qdrant thực tế có 14.393 point. Readiness hiện
  phải fallback sang Qdrant collection metadata. Đây là release-contract bug,
  cần sửa trước production.
- `datasets.collection_name` vẫn là tên pgvector legacy
  `legal_graph_chunks__snapshot_c439751724ab7f10`, không phải physical Qdrant
  collection thực tế.
- Cả 37.170 `chunks.embedding_input_sha256` đang rỗng. Runtime vì vậy bỏ qua
  Qdrant input-hash comparison thay vì thực sự chứng minh vector và canonical
  passage cùng một input.
- `chunks.id` và `chunks.source_key` giống hệt nhau ở 37.170/37.170 rows và đều
  chỉ là `dataset_id:chunk_id`; hai cột cùng hai unique indexes là trùng lặp.
- `embedding_input_text` rỗng 37.170/37.170 rows; các cột pgvector/embedding
  metadata không còn thuộc runtime contract sau khi chuyển sang Qdrant.
- `payload` của `legal_units`, `document_tables`, `table_cells` rỗng 100%; còn
  `chunks.payload` chỉ giữ ba field có schema ổn định và nên thành typed columns.
- Supabase chỉ còn một release, nên hiện chưa có rollback release tại chỗ.
- Các manifest trung gian còn chênh 1 document semantic-eligible và số reference
  node; cần một final manifest duy nhất làm machine-readable authority.

### 3.2 Qdrant

- Stable alias: `medical_legal_active`.
- Physical release collection: `medical_legal_snapshot-c439751724ab7f10`.
- 14.393 vectors, 1.536 dimensions, `text-embedding-3-small`.
- Collection `green`, 14.393/14.393 vectors indexed, 2 segments, không warning.
- Payload indexes: `dataset_id`, `document_id`, `answer_ready`,
  `retrieval_scope`, `legal_status`, `categories`.
- Chỉ có một physical collection; không có collection rollback online.
- ANN document overlap với exact Qdrant: `0,997552`.
- Thematic benchmark 80 câu:
  - Recall@1: 53,75%
  - Recall@5: 81,25%
  - Recall@10: 83,75%
  - Recall@20: 88,75% — pass gate 85%

Kết luận: chưa có bằng chứng để re-embed toàn bộ ngay. Vấn đề hiện tại chủ yếu
là routing, passage scope, reranking, latency và answer generation; không phải
vector artifact hỏng.

### 3.3 Neo4j

Live read-only inventory cho thấy Neo4j đang giữ hai releases:

| Dataset | Nodes | Relationships | Vai trò |
|---|---:|---:|---|
| `snapshot-c439751724ab7f10` | 1.901 | 5.816 | active: 5.808 legal + 8 alias |
| `snapshot-c94d7b75195a67fa` | 1.901 | 5.817 | stale/rollback candidate |

Trong active release:

- 690 canonical/alias document nodes và 1.211 reference-only nodes.
- 5.808 legal relationships + 8 alias relationships.
- Chỉ khoảng 187 cạnh `approved_evidence` được phép online serving.
- Hơn 5.600 cạnh audit-only phải được giữ cho audit nhưng không được dùng làm
  answer evidence.

Runtime hiện đã lọc `rel.serving_status = 'approved_evidence'`, giới hạn hop và
re-retrieve text từ Supabase/Qdrant. Đây là boundary đúng; không chuyển graph
text thành nguồn pháp lý.

Release `snapshot-c94d7b75195a67fa` là phần thừa duy nhất đã xác định chắc ở
database live, nhưng chưa được xóa trước khi export, checksum và restore drill
pass. Qdrant và Supabase hiện không có object/release thừa tương ứng để xóa.

## 4. Chất lượng và eval: những gì được phép khẳng định

### Kết quả có giá trị hiện tại

- Exact identifier: 100/100.
- Graph evidence parity: 100/100.
- Thematic Qdrant Recall@20: 88,75%, pass.
- Qdrant ANN-vs-exact overlap: 99,755%.
- Release-locked suite có 292 cases và hash contract.
- Targeted live regression Quảng Ngãi: đủ 8/8 legal units, 8 citations, không
  fallback và không lộ `EVIDENCE_ID`/`DOCUMENT_ID`.

### Kết quả không được dùng làm current quality claim

- Run RAGAS 0/36 trong `run-20260816-full-36` là historical evidence trước khi
  Qdrant/runtime hiện tại được hoàn thiện. Không xóa, nhưng không dùng làm điểm
  hiện tại.
- `local-active-release.json` báo semantic Recall@10 13% vì dùng câu exact
  document-number để chấm semantic-only. Exact query phải đi exact route; metric
  này không được làm semantic release gate.
- File test release-locked hiện chủ yếu xác nhận dataset/hash/taxonomy, chưa đồng
  nghĩa 292 câu đã chạy end-to-end và pass.

### Khoảng trống bắt buộc

Chưa thể tuyên bố “data/answer chính xác 100%” cho đến khi có:

1. 200–300 câu human-adjudicated, khóa theo active release.
2. Retrieval ID/span metrics và answer metrics tách riêng.
3. Current end-to-end run sau commit `d51c416`.
4. Ba run lặp để kiểm tra variance.
5. Temporal/status cases có chuyên gia hoặc nguồn chính thức xác nhận.

## 5. Bottleneck của request khoảng 12 giây

### 5.1 Số đo live ngày 2026-08-19

Query đo: danh sách đối tượng được hỗ trợ BHYT theo Nghị quyết
`60/2026/NQ-HĐND`.

| Stage | Cold/first | Warm/median | Ghi chú |
|---|---:|---:|---|
| Supabase active release | 2,24 giây | 607 ms | RTT cao; cache runtime 30 giây |
| Supabase lexical sample | 203 ms | chưa đủ mẫu | Chạy song song với embedding |
| OpenAI query embedding | 1,68 giây | 460 ms | Runtime đã cache cùng query 5 phút |
| Qdrant search | 2,95 giây | 247 ms | Có outlier 12,99 giây |
| LLM generation | 6,91–7,58 giây | khoảng 7,24 giây | Context 3.731 chars, answer ~1.450 chars |
| Full live answer đã quan sát | 12,78 giây | phụ thuộc connection/cache | Không streaming |

### 5.2 Kết luận bottleneck

**Bottleneck ổn định số 1: LLM generation.** Nó chiếm khoảng 55–60% tổng
12,8 giây. Context chỉ 3.731 ký tự nên nguyên nhân chính không phải prompt quá
lớn; nguyên nhân là chờ full completion khoảng 1.450 ký tự trước khi API trả JSON.

**Bottleneck p95 số 1: Qdrant cold/network outlier.** Warm search chỉ khoảng
0,25 giây, nhưng first request khoảng 3 giây và đã có một lần 12,99 giây.
Điều này chỉ ra connection/TLS/region/free-cluster variability, không phải HNSW
compute trên corpus 14k vector.

**Bottleneck cold-start số 2: Supabase + embedding.** Active release query lạnh
~2,24 giây và embedding lạnh ~1,68 giây. Pipeline hiện lấy release trước, sau đó
mới chạy lexical + embedding; Qdrant phải chờ embedding xong.

LangGraph orchestration không phải bottleneck đáng kể. Graph hiện là một flow
tuyến tính; chi phí chủ yếu nằm ở network providers và LLM.

### 5.3 Các nguyên nhân trong code

1. `get_llm()` tạo `ChatOpenAI` mới cho mỗi generation, không có singleton/client
   reuse rõ ràng.
2. API không streaming; frontend phải chờ full answer.
3. Không có `max_tokens`/answer-length budget cho route extractive.
4. Qdrant/OpenAI/Supabase clients không được prewarm trong lifespan.
5. `pool_pre_ping=True` có thể thêm một database round trip trên mỗi checkout.
6. `_retrieve()` giữ một Supabase session trong khi chờ embedding/Qdrant, làm
   connection pool 5 slot dễ nghẽn khi concurrent traffic tăng.
7. Hydrate và sibling-scope expansion là hai SQL round trips tuần tự.
8. [Resolved 2026-08-22] Retrieval và generation cache đều đã được bounded theo
   immutable release, normalized query, model/config/policy fingerprint và
   context digest; answer cache chỉ áp dụng cho low-risk/public context
   release-scoped, không áp dụng temporal/high-risk.
9. [Resolved 2026-08-22] Active release cache vẫn revalidate bounded 30 giây,
   nhưng mọi retrieval/answer entry đều mang release namespace nên không thể
   phục vụ mixed/stale release sau cutover.
10. Actual `.env` từng bật LangSmith với key lỗi 403 trong khi dự án dùng
    Langfuse; retry/logging này phải tắt hoàn toàn.
11. Qdrant hiện ở `eu-west-2`, Langfuse ở `jp`; region deploy của backend và
    Supabase chưa được ghi thành contract. Cần đo từ nơi deploy thật rồi
    colocate backend gần canonical DB và vector store.

### 5.4 Latency target thực tế

Không nên đặt full completion `<900 ms` cho câu semantic có LLM dài. Nên tách:

| SLO | Mục tiêu sau tối ưu |
|---|---:|
| Policy/exact deterministic p95 | <700 ms nếu colocated, <1.200 ms cross-region |
| Semantic retrieval p95 warm | <1.500 ms |
| Time-to-first-token p95 | <2.500 ms |
| Full concise answer p50 | <5.000 ms |
| Full concise answer p95 | <8.000 ms |
| Qdrant search p95 warm | <600 ms |
| Dependency timeout/circuit-breaker | fail fast, không treo 30–45 giây |

## 6. Có nên đưa về DDD không?

### Quyết định

**Có áp dụng DDD có chọn lọc; không full rewrite DDD.** Dự án phù hợp với
modular monolith vì team nhỏ, một backend và workload còn thay đổi nhanh. Full
DDD/microservices lúc này làm tăng module, DTO, event và deployment overhead mà
không trực tiếp tăng recall hay giảm latency.

### Bounded contexts đề xuất

1. **Corpus Release**
   - Build canonical corpus, validate, publish/cutover/rollback.
   - Aggregate/invariant chính: `DatasetRelease` phải đồng nhất Supabase,
     Qdrant alias và Neo4j dataset.

2. **Legal Evidence Retrieval**
   - Query planning, exact/lexical/semantic, PageIndex, graph navigation,
     fusion, evidence selection.
   - Domain objects: `LegalIdentifier`, `LegalUnitPath`, `Evidence`,
     `EvidenceBundle`, `RetrievalPlan`.

3. **Answering & Safety**
   - Policy routing, evidence verification, answer formatting, claim/citation
     validation và streaming.
   - Domain objects: `AnswerPolicy`, `VerifiedClaim`, `Citation`, `Answer`.

4. **Evaluation & Quality**
   - Gold set, experiments, release gates, Langfuse scores và regression.

5. **Identity/Admin** — chỉ khi Firebase/admin thật được triển khai.

### Ports nên có

- `CorpusReadPort` — Supabase exact/lexical/hydration/legal units.
- `SemanticSearchPort` — Qdrant.
- `RelationshipGraphPort` — Neo4j.
- `EmbeddingPort` — OpenAI embedding.
- `AnswerModelPort` — chat model/streaming.
- `TracePort` — Langfuse.
- `ReleaseRegistryPort` — active release fingerprint/parity.

### Application use cases

- `AnswerLegalQuestion`
- `ResolveDocumentMetadata`
- `RetrieveVerifiedEvidence`
- `PublishCorpusRelease`
- `RollbackCorpusRelease`
- `EvaluateRelease`

### Cấu trúc đích, chuyển dần

```text
src/
  domain/
    release/
    legal_evidence/
    answering/
  application/
    answer_legal_question.py
    retrieve_evidence.py
    publish_release.py
  ports/
    corpus.py
    semantic.py
    graph.py
    models.py
    tracing.py
  infrastructure/
    supabase/
    qdrant/
    neo4j/
    openai/
    langfuse/
  interfaces/
    http/
```

Không cần đổi toàn bộ đường dẫn ngay. Dùng strangler pattern: tạo port và use
case quanh `GraphRagRuntime`, chuyển từng dependency, giữ API contract và test
đến khi `src/services/chat.py` chỉ còn orchestration mỏng.

### Những thứ không nên làm

- Không tạo repository cho từng table chỉ để “đúng DDD”.
- Không biến Supabase/Qdrant/Neo4j thành ba microservice do team tự vận hành.
- Không đưa graph edges vào domain aggregate mutable.
- Không dùng event bus/Kafka khi release pipeline vẫn chạy batch đơn.
- Không full rewrite LangGraph trước khi có eval chứng minh lợi ích.

## 7. Cơ chế hội thoại người dùng và batching LLM

### 7.1 Hiện trạng: có UI hội thoại nhưng backend vẫn stateless

Luồng hiện tại chưa phải multi-turn chat thật:

- `web/app/page.tsx` chỉ giữ messages trong React state; refresh/new device là
  mất toàn bộ hội thoại.
- Frontend gửi toàn bộ `chat_history`, kể cả message hiện tại, qua
  `web/lib/api.ts`.
- `ChatRequest` cho phép từng message dài tối đa 5.000 ký tự nhưng không giới
  hạn số message hoặc tổng payload.
- `src/api/routes.py` chỉ truyền `request.message` vào agent và bỏ hoàn toàn
  `request.chat_history`.
- Agent state không có `conversation_id`, resolved query, conversation summary,
  document đang được nhắc tới hoặc citations của lượt trước.
- Retrieval và generation chỉ nhìn câu hiện tại. Các câu như “còn khoản 2?”,
  “văn bản này còn hiệu lực không?” hoặc “nhóm thứ ba thì sao?” vì vậy không có
  đủ ngữ cảnh để xử lý ổn định.
- Langfuse đang dùng HTTP `request_id` làm `session_id`; mỗi turn bị hiển thị
  như một conversation riêng nên không phân tích được hành trình người dùng.

Nếu chỉ nối toàn bộ transcript vào prompt, hệ thống sẽ tăng token/latency/cost,
dễ tin lại câu trả lời sai trước đó và vẫn không giải quyết được provenance.

### 7.2 Nguyên tắc thiết kế

1. Conversation memory chỉ dùng để hiểu người dùng đang nhắc tới cái gì; nó
   **không bao giờ là legal evidence**.
2. Mỗi turn phải retrieve lại từ active corpus/release. Câu trả lời cũ của
   assistant không được dùng làm nguồn pháp lý.
3. Server là nguồn canonical cho conversation state; client không gửi lại vô
   hạn toàn transcript.
4. Lưu riêng user intent/anchor IDs với evidence/citations. Không biến một bản
   tóm tắt do LLM tạo thành “sự thật”.
5. Ưu tiên reference resolution deterministic từ citation/document/legal-unit
   IDs của turn trước. Chỉ gọi model rewrite nhỏ khi câu thực sự phụ thuộc ngữ
   cảnh và rule không giải được.
6. Nếu “văn bản này” có thể chỉ nhiều tài liệu, hỏi lại thay vì đoán.
7. Memory phải có ownership, retention, redaction và delete policy; không lưu
   PII/hóa đơn y tế lâu dài theo mặc định.

### 7.3 Contract và data model đích

API streaming nên nhận:

```json
{
  "message": "Còn khoản 2 thì sao?",
  "conversation_id": "uuid-or-null"
}
```

Server tạo `conversation_id` ở turn đầu và luôn trả lại cùng `turn_id`. Sau giai
đoạn migration, bỏ `chat_history` khỏi public contract. Trong thời gian tương
thích, giới hạn tối đa 12 messages và 20.000 ký tự tổng; không tin nội dung role
`assistant` do client gửi lên.

Hai bảng nhỏ trong Supabase là đủ cho giai đoạn hiện tại; chưa cần thêm Redis:

- `conversations`: `id`, `user_id` hoặc signed anonymous owner, timestamps,
  rolling intent summary, summary version, retention deadline.
- `conversation_turns`: sequence, raw query, resolved standalone query, answer,
  cited document/legal-unit/evidence IDs, corpus release ID, trace ID, timestamps.

Áp dụng RLS theo owner. Không cho truy cập conversation chỉ vì biết UUID. Khi
auth chưa hoàn tất, chỉ dùng signed HttpOnly anonymous-session token và TTL ngắn;
không mở persistence công khai bằng một `conversation_id` trần.

Luồng một turn:

1. Xác thực user/session và ownership của conversation.
2. Load tối đa 4–6 turns gần nhất, rolling intent summary và structured anchors
   của turn trước; không load toàn transcript.
3. Resolve đại từ/tham chiếu bằng prior cited IDs. Với câu self-contained, bỏ
   qua toàn bộ bước rewrite.
4. Khi cần, sinh `resolved_query` bằng model nhỏ; model này chỉ rewrite, không
   được trả lời câu hỏi.
5. Retrieve evidence mới trên active release bằng `resolved_query`.
6. Generate từ current evidence + phần user context tối thiểu cần thiết.
7. Lưu turn, citations, release/fingerprint và trace; cập nhật summary qua job
   bất đồng bộ sau mỗi N turns, không chặn response.

Langfuse phải dùng stable `conversation_id` làm `session_id`; `request_id`/`turn_id`
vẫn là trace/span identifier. Retrieval cache key là:

```text
release_fingerprint + normalized_resolved_query + retrieval_policy_version
```

Không đưa raw transcript vào cache key và không dùng shared answer cache cho
answer có context cá nhân. Conversation memory thuộc bounded context Answering,
không cần tạo microservice hoặc bounded context mới ở giai đoạn này.

### 7.4 Batch đúng chỗ, không batch final chat của nhiều user

Batching không phải giải pháp trực tiếp cho request 12 giây. Final generation là
bottleneck ổn định; chờ gom nhiều request rồi mới gửi sẽ làm TTFT tệ hơn, khó
cancel, khó trace và tăng phạm vi ảnh hưởng khi lỗi. Interactive path nên dùng
streaming, connection reuse, bounded concurrency và backpressure.

| Workload | Cơ chế nên dùng |
|---|---|
| Một câu chat tương tác | Gọi trực tiếp + SSE; không chờ batch |
| Nhiều user đồng thời | Async concurrency với semaphore/quota theo provider |
| Một turn được tách thành nhiều sub-query | Embed các sub-query trong một embeddings request, rồi Qdrant batch/concurrent search |
| Một embedding query thông thường | Gọi trực tiếp; chỉ micro-batch 5–15 ms nếu load test ở QPS thật chứng minh có lợi |
| Nhiều claims cần verify trong một answer | Một structured verifier call chứa toàn bộ claims, không N calls nối tiếp |
| Tóm tắt conversation cũ | Durable background job sau response; có thể gom nhiều job |
| RAGAS, enrichment, re-embed, metadata repair | Batch/offline job với checkpoint, retry và concurrency limit |

Không dùng provider Batch API cho live chat vì đây là đường xử lý bất đồng bộ,
phù hợp với eval/enrichment/re-embed hơn là latency tương tác. Cũng không chạy
`asyncio.gather` không giới hạn: cần semaphore riêng cho embedding, Qdrant và
answer model, queue depth metric, timeout budget và trả `429/503` có kiểm soát
khi quá tải.

Các tối ưu batching có ROI cao nhất cho repo này:

- Multi-query retrieval: một embedding batch thay vì nhiều lần gọi nối tiếp.
- Qdrant batch query chỉ khi một turn có nhiều sub-query; một query đơn không
  được lợi đáng kể.
- Gộp claim verification thành một structured LLM call.
- Nightly eval/enrichment/re-embed chạy offline batch, checkpoint được và không
  tranh quota với traffic production.
- Single-flight cho embedding/retrieval giống nhau đang xảy ra đồng thời; đây
  thường hiệu quả hơn đợi gom final chat completions.

### 7.5 Eval và release gates cho multi-turn

Tạo ít nhất 50 conversation scenarios có human expected result, gồm:

- pronoun/document reference: “văn bản này”, “nghị quyết trên”;
- legal hierarchy: “còn khoản 2?”, “điểm b thì sao?”;
- correction và topic switch;
- nhiều possible anchors buộc phải clarify;
- câu trả lời sai ở turn trước không được tái sử dụng như evidence;
- release cutover giữa hai turns;
- conversation của user A tuyệt đối không đọc được bởi user B;
- retention/delete và payload-limit tests.

Gate đề xuất:

- multi-turn context-resolution accuracy ≥95% trên bộ 50 scenarios;
- unsupported claim lấy từ conversation memory = 0;
- cross-user conversation leakage = 0;
- self-contained query không phát sinh rewrite LLM call;
- memory load + deterministic resolution p95 <250 ms;
- full concise answer vẫn giữ p95 <8 giây;
- một conversation được nhóm đúng thành một Langfuse session, mỗi turn là một
  trace riêng.

## 8. Tái cấu trúc database và chuẩn bị full Docker

### 8.1 Quyết định kiến trúc dữ liệu

Không hợp nhất cả ba database vào Qdrant và cũng không dựng thêm một database
thứ tư để “làm sạch”. Target vẫn là ba physical stores, nhưng mỗi store chỉ có
một trách nhiệm:

| Store | Dữ liệu sở hữu | Không được sở hữu |
|---|---|---|
| PostgreSQL/Supabase | canonical text/HTML, metadata, legal hierarchy, lexical index, release registry, conversation/audit state | vector, graph relationship runtime |
| Qdrant | immutable semantic projection của passages | canonical text, conversation, release authority |
| Neo4j | immutable document/reference graph projection | quoted evidence, user state, active-release authority |

`release_key` và fingerprint nối ba stores. PostgreSQL giữ active pointer duy
nhất; Qdrant và Neo4j là projections có thể rebuild từ canonical source +
artifact. Qdrant outage làm mất semantic recall nhưng không được làm mất dữ liệu
gốc. Neo4j outage làm mất graph expansion nhưng không được làm mất exact/lexical.

Không self-host toàn bộ Supabase stack chỉ để chạy Docker. Code hiện không dùng
`SUPABASE_URL`/`SUPABASE_ANON_KEY` ở runtime, mà truy cập PostgreSQL trực tiếp.
Local full-stack vì vậy chỉ cần PostgreSQL chuẩn; production có thể tiếp tục
dùng managed Supabase. Nếu sau này thực sự dùng PostgREST/Storage/Auth thì thêm
chúng như một quyết định riêng, không kéo cả Supabase self-host vào mặc định.

### 8.2 Debt cần loại bỏ trước khi đổi schema

1. Có hai schema authorities: `database/postgres/schema.sql` và hàng trăm dòng DDL trong
   `create_dataset_schema()` của `database/pipeline/data_pipeline/storage.py`.
   Chúng đã lệch nhau về cột `embedding`.
2. Runtime dùng Qdrant nhưng pipeline chính, docs, tests và một số eval vẫn dùng
   pgvector. Publication contract hiện có hai đường mâu thuẫn.
3. `database/postgres/schema.sql` vẫn cài extension vector; dependency set
   legacy trước đây từng đưa `pgvector` vào production image dù active
   Supabase có 0 vectors. Runtime lock hiện không còn pgvector.
4. `chunks.id`, `chunks.source_key` và hai indexes tương ứng trùng hoàn toàn;
   `embedding_input_text` và toàn bộ vector metadata hầu như vô nghĩa sau
   Qdrant cutover.
5. High-value metadata (`so_ky_hieu`, ngày, status, `answer_ready`, scope) nằm
   trong nested JSONB. Exact queries phải lặp `COALESCE`/`regexp_replace` và
   không có typed constraints.
6. `section_title` lặp trên từng passage; ba field ổn định lại bị nhét vào
   `chunks.payload`. Nhiều bảng có `payload` rỗng 100%.
7. `search_vector` được update thủ công; pipeline crash có thể để lexical text
   và FTS index lệch nhau.
8. Supabase manifest không biết physical Qdrant collection/count/hash; field
   `collection_name` vẫn trỏ tên pgvector legacy.
9. Supabase passage hash dùng để đối chiếu Qdrant đang rỗng, khiến runtime hash
   check chỉ là optional check.
10. `src/db/models.py` chỉ mô tả một phần schema; raw SQL runtime, raw SQL
    pipeline và ORM model không có một migration contract chung.
11. Hotfix scripts mutate active PostgreSQL/Neo4j trực tiếp. Việc này có thể
    làm canonical source, Qdrant payload và graph properties khác release
    fingerprint mà không tạo release mới.
12. Local workspace giữ hơn 30 full derived snapshots (~2,4 GB), backup JSON
    ~466 MB và `.venv` ~7,1 GB. `.dockerignore` chưa loại `data/clean/` hoặc
    `database/backups/`, nên Docker build context có thể vô tình vượt 3 GB.

### 8.3 Target schemas và bảng

Tách ba PostgreSQL schemas theo ownership:

```text
ops       release registry, projection status, migration/outbox/audit
corpus    immutable legal corpus and lexical/PageIndex read model
app       conversations, turns, user/admin workflow
public    chỉ stable views/RPC thật sự cần expose; mặc định không có base table
```

Tên cụ thể có thể thay đổi trong migration, nhưng boundary và invariants không
được thay đổi.

#### `ops.corpus_releases`

- `id bigint generated always as identity` — internal PK nhỏ, không đưa ra API.
- `release_key text unique` — giá trị như `snapshot-c439751724ab7f10`, dùng ở
  Qdrant/Neo4j/eval.
- `fingerprint char(64) unique`, `source_manifest_sha256 char(64)`.
- `status`: `building | staged | verified | active | superseded | failed`.
- `schema_version`, `pipeline_version`, `normalizer_version`, `parser_version`,
  `chunker_version`, `source_as_of_date`.
- `created_at`, `verified_at`, `published_at`, `failure_reason`.
- `manifest jsonb` chỉ giữ audit extras; các field dùng cho correctness không
  được giấu duy nhất trong JSON.

#### `ops.release_projections`

Một row cho mỗi `(release_id, store)` với `store = postgres | qdrant | neo4j`:

- `physical_locator`: schema/table set, Qdrant collection hoặc Neo4j database.
- `status`: `pending | loading | ready | failed | retired`.
- `expected_count`, `actual_count`, `content_fingerprint`.
- model/dimensions/artifact hash cho Qdrant; node/edge counts cho Neo4j.
- `verified_at`, `verification_report`, `error`.

Bảng này thay `datasets.collection_name` legacy và field count mơ hồ trong
manifest. Active release không được chọn nếu một required projection chưa
`ready` hoặc fingerprint/count không khớp.

#### `ops.active_release`

Một singleton row gồm `release_id`, monotonic `generation`, `activated_at` và
`previous_release_id`. Publication chỉ update row này sau khi mọi projection đã
ready. Runtime đọc một `ActiveRelease` value object chứa release key,
fingerprint, physical Qdrant collection và expected counts.

#### `corpus.documents`

Dùng internal `document_pk bigint` để child tables không lặp text ID trong mọi
index. Giữ `document_key text` là ID ổn định/citation ID với unique constraint
`(release_id, document_key)`.

Typed columns bắt buộc:

- `title`, `normalized_title`, `so_ky_hieu`, `normalized_so_ky_hieu`;
- loại văn bản, cơ quan, jurisdiction/category;
- raw date text + parsed date + parse status cho ban hành/có hiệu lực/hết hiệu
  lực; không ép một ngày mơ hồ thành `date`;
- `legal_status`, `legal_status_verified`, `status_checked_at`;
- `answer_ready`, `index_eligible`, `lexical_eligible`, `semantic_eligible`,
  `retrieval_scope`;
- `content_text`, `raw_html`, source URL/kind và SHA-256 tương ứng;
- `provenance jsonb` chỉ cho metadata hiếm/audit, không lặp `id`, `title`, số ký
  hiệu hoặc typed fields.

Giữ HTML trong PostgreSQL ở quy mô hiện tại: raw HTML chỉ khoảng 7,3 MB và giúp
rebuild/citation audit độc lập. Chỉ chuyển HTML sang object storage nếu storage
benchmark sau schema v2 chứng minh cần; không đánh đổi provenance để tiết kiệm
vài MB sớm.

#### `corpus.document_aliases`

Giữ alias key, canonical `document_pk`, type, confidence, reason, evidence URL
và hash. Alias không được tạo searchable duplicate hoặc graph fact mới.

#### `corpus.legal_units`

- Internal `unit_pk bigint`; stable `unit_key` unique theo release.
- `document_pk`, nullable `parent_unit_pk`, type/ordinal/label/heading.
- source start/end, selector, fragment/text hashes, parser version/confidence.
- `text` nullable: prose lấy từ canonical document span; chỉ table/derived unit
  cần materialized text.
- Bỏ `payload` nếu vẫn rỗng sau migration audit.

Trước khi bỏ một trong `label`/`heading`, phải đo equality theo unit type. Hiện
hai cột chiếm khoảng 10,6 MB cộng lại nhưng không được collapse chỉ dựa vào kích
thước nếu chúng mang semantics khác nhau.

#### `corpus.document_tables` và `corpus.table_cells`

- `document_tables` dùng internal `table_pk bigint`, stable `table_key`,
  `document_pk`, ordinal, selector/hash, dimensions và extractor version.
- `table_cells` PK chỉ còn `(table_pk, row_index, column_index)`; không lặp
  `dataset_id` và text `table_id` trong 90.438 rows/index.
- Thay `cell_tag` bằng typed `is_header` nếu parity chứng minh đủ; `colspan` và
  `rowspan` giữ default 1.
- Bỏ payload rỗng. Header/row-header chỉ materialize khi thực sự có giá trị.

#### `corpus.passages` thay cho `chunks`

- `passage_id` ổn định 32 hex, `release_id`, `document_pk`, `unit_pk`, order.
- `passage_kind`, nullable `table_pk`/`table_row_index` thành typed columns.
- text, source spans, `text_sha256`, parser/chunker version.
- `lexical_eligible`, `semantic_eligible`.
- `embedding_input_sha256` bắt buộc cho semantic passage, null cho ineligible.
- generated stored `search_vector`; pipeline không update FTS thủ công.
- Bỏ `id`, `source_key`, `embedding_input_text`, `embedding`, toàn bộ per-row
  embedding model metadata, duplicated `section_title` và generic `payload`.

Section title được join từ legal unit hoặc materialize trong một read view nếu
benchmark chứng minh join tốn đáng kể. Model/dimensions/preprocessor thuộc
Qdrant projection/release artifact, không lặp 14.393 lần trong PostgreSQL.

#### `app.conversations` và `app.conversation_turns`

Dùng schema từ mục 7: owner/RLS, bounded summary, resolved query, citation IDs,
release ID, trace và retention deadline. Conversation tables không FK trực tiếp
tới mutable active pointer; mỗi turn giữ immutable `release_id` đã dùng. Không
đưa conversation text vào corpus schema hoặc Qdrant legal collection.

### 8.4 Constraints, indexes và access contract

- Tạo domain/check cho SHA-256 lowercase 64 hex.
- Check `source_start >= 0`, `source_end > source_start` khi có span.
- Check semantic passage có `embedding_input_sha256`; ineligible passage không
  được có vector metadata.
- Unique `(release_id, document_key)`, `(release_id, unit_key)` và
  `(release_id, passage_id)`; không tạo index tương đương lần hai.
- B-tree exact index cho normalized số ký hiệu; title fallback chỉ thêm trigram
  sau `EXPLAIN (ANALYZE, BUFFERS)` chứng minh cần.
- Partial GIN FTS index cho lexical-eligible passages. `search_vector` generated
  từ canonical passage text để không drift.
- Index `(release_id, document_pk, passage_order)` và
  `(release_id, parent_unit_pk, source_start)` cho hydrate/scope expansion.
- Không index JSONB toàn cột. Chỉ expression index cho field audit có query thật.
- App runtime role chỉ có SELECT trên active read views/functions và CRUD trên
  conversation rows thuộc owner.
- Vì auth hiện là Firebase chứ không phải Supabase JWT, mỗi conversation
  transaction phải dùng `SET LOCAL app.user_id` sau backend token verification
  và RLS đọc `current_setting`; không dùng session-level `SET` trên pooled
  connection. Repository vẫn luôn predicate theo `user_id` như lớp bảo vệ đầu.
- Publisher role được insert immutable staging release và update release state;
  không phải app runtime credential.
- Migration owner riêng; backend container không có quyền DDL, `DROP`, bypass
  RLS hoặc mutate active corpus.

`SUPABASE_ANON_KEY` không thay thế PostgreSQL least-privilege role. Nếu backend
chỉ dùng direct SQL, loại hai Supabase REST variables khỏi runtime sau khi xác
minh không consumer nào dùng chúng.

### 8.5 Publication không phụ thuộc distributed transaction

Không thể atomically commit PostgreSQL, Qdrant và Neo4j cùng lúc. Target flow:

1. Build canonical source + release manifest deterministically.
2. Insert immutable PostgreSQL shadow release, validate all FK/hash/counts.
3. Tạo physical Qdrant collection theo `release_key`; upload và verify toàn bộ
   passage ID/input hash/vector sample.
4. Import Neo4j nodes/edges theo cùng `release_key`; verify endpoint/type/count.
5. Ghi cả ba `release_projections=ready` cùng verification reports.
6. Smoke/eval trực tiếp physical release, chưa ảnh hưởng traffic.
7. Trong PostgreSQL transaction có advisory lock, update một
   `ops.active_release` pointer.
8. Runtime lấy physical Qdrant collection từ active release registry và filter
   Neo4j bằng release key.

Stable Qdrant alias có thể giữ cho ops/manual inspection nhưng không nên là
correctness dependency. Query physical collection từ registry giúp Supabase
active pointer trở thành cutover duy nhất; nếu alias đổi chậm cũng không tạo
mixed release. Mọi evidence vẫn mang release key/fingerprint và bị reject nếu
khác request release.

Rollback chỉ update active pointer về `previous_release_id` sau readiness check
trên cả ba physical projections. Vì vậy ít nhất active + một rollback projection
phải còn tồn tại. Không gọi thao tác “rollback” nếu nó cần re-embed/re-import từ
đầu.

### 8.6 Migration strategy: shadow schema, không ALTER mù production

Không normalize 170 MB live tables bằng một chuỗi destructive `ALTER/DROP`.
Dùng expand → copy → verify → cutover → contract:

#### DB-0 — Freeze và backup

- Khóa release key/fingerprint hiện tại và lưu live inventory.
- Tạo compressed `pg_dump --format=custom` cho schema/data active thay vì JSON
  466 MB; lưu SHA-256 và test restore vào PostgreSQL tạm.
- Export active và stale Neo4j releases hoặc xác nhận có thể rebuild byte-for-
  byte từ `relationships.csv` + manifest.
- Giữ Qdrant embedding artifact hiện có:
  `data/clean/embeddings-reused/snapshot-c439751724ab7f10`.
- Không rotate/delete cloud objects trước restore drill.

#### DB-1 — Một migration authority

- Dùng Alembic với explicit SQL migrations; không dựa vào autogenerate cho
  views, RLS, generated FTS và extensions.
- `database/postgres/migrations/` là authority duy nhất; mỗi migration có checksum,
  precondition và transaction policy.
- `database/postgres/schema.sql` chỉ là generated current-schema snapshot cho review hoặc
  bị bỏ; không còn hướng dẫn “apply once”.
- Xóa DDL khỏi `create_dataset_schema()`; runtime/pipeline không tự migrate.
- Production migration chạy bằng one-shot `migrate` container có advisory lock.
- Production ưu tiên forward fix. Downgrade chỉ được khai báo khi thật sự khôi
  phục được data; rollback file tạo lại table không đồng nghĩa restore rows.

#### DB-2 — Tạo `ops/corpus/app` shadow schemas

- Chỉ tạo structure, roles, grants và views; chưa đổi production reads.
- Load active canonical source vào schema v2 bằng internal bigint keys.
- Backfill Qdrant `embedding_input_sha256` từ artifact theo passage ID; bắt buộc
  14.393/14.393 match Qdrant payload trước khi tiếp tục.
- Không re-embed nếu passage ID, input hash, model và dimensions vẫn khớp.

#### DB-3 — Parity và dual-read

- So sánh counts, IDs, content/hash, unit parent/span, table cells, lexical top-k
  và exact results giữa old/v2.
- Shadow runtime chạy toàn deterministic suite, 80 semantic cases, targeted E2E
  và 100-query latency profile.
- Read-only dual-read sampling ghi diff; không dual-write mutable corpus.

#### DB-4 — Cutover

- Preflight `pg_database_size`; đặt hard stop ở 450 MiB để còn headroom cho WAL,
  indexes và transaction temp. Không dựa vào ước lượng CSV.
- Switch repository feature flag/read view sang schema v2.
- Theo dõi error, exact/semantic parity và citation hashes.
- Giữ old schema/read path trong rollback window; không drop cùng ngày.

#### DB-5 — Contract và reclaim

- Sau tối thiểu một restore/rollback drill và thời gian quan sát đã định, revoke
  old writes, export checksum, drop old views/tables/pgvector columns.
- Chỉ drop extension `vector` khi dependency query xác nhận không object nào dùng.
- Chạy `VACUUM (ANALYZE)`; `VACUUM FULL` chỉ trong maintenance window và khi
  đo thực tế chứng minh cần lock/rewrite table.
- Cập nhật models, repositories, pipeline, docs và eval cùng migration commit.

### 8.7 Canonical source và artifact lineage

Không tiếp tục tạo dây chuyền full-copy `medical_active_v2` → `v31`. Target tách
source, reviewed corrections và generated artifacts:

```text
data/source/                 immutable upstream snapshots
data/curation/
  document_corrections.csv
  aliases.csv
  legal_status_evidence.jsonl
  relationship_corrections.csv
  review_exceptions.csv
data/fixtures/               corpus nhỏ cho integration tests
var/artifacts/               generated, content-addressed, gitignored
```

Mỗi correction record cần correction ID, target ID/field, old/new value hoặc
operation, evidence URL/hash, reviewer/status, timestamp và input source hash.
Không cho script hotfix ghi thẳng active DB rồi mới cố đồng bộ ngược về CSV.
Publication luôn build lại từ immutable source + ordered approved corrections;
manifest chứa hash của mọi input.

Migration một lần phải đóng gói trạng thái v31 thành baseline có provenance,
sau đó chứng minh rebuild tạo đúng 682 documents, 37.170 passages, 28.285 units,
5.808 legal relationships và cùng canonical hashes. Chỉ khi fingerprint/parity
pass mới archive chuỗi v2–v30. Backup database không được coi là canonical
source; nó chỉ phục vụ disaster recovery.

### 8.8 Retention và cleanup policy

Supabase Free 500 MB không đủ để giữ vô hạn immutable releases. Policy:

- At rest: active + tối đa một rollback release.
- Trước khi stage release thứ ba: backup/restore-test rồi prune oldest
  superseded release.
- Trong migration: phải đo active + shadow + index/WAL headroom; dừng trước
  450 MiB.
- Failed staging release bị xóa sau report/checksum, không giữ vô hạn trong DB.
- Release metadata/audit nhỏ được giữ lâu hơn data rows.

Inventory cleanup cụ thể:

| Target | Quyết định |
|---|---|
| Supabase active release | giữ; hiện không có stale rows để xóa |
| Supabase pgvector columns/extension | drop sau DB-4 và sau khi xóa mọi legacy caller |
| Qdrant active collection | giữ; hiện không có collection thừa |
| Neo4j `snapshot-c94d7b75195a67fa` | export + checksum + restore/rebuild test, rồi xóa nếu active + rollback policy đã có nơi khác |
| `data/clean/medical_active_v2`…`v30` | rebuildable; archive theo checksum rồi chuyển vào trash, giữ v31/current artifact/benchmarks |
| Local embedding artifacts | giữ active + rollback; dedupe theo artifact SHA-256 |
| Backup JSON ~466 MB | đổi sang compressed dump/export, verify restore rồi mới xóa bản JSON |
| `.venv` ~7,1 GB | chỉ xóa sau khi locked local/container environment chạy được |

Không dùng shell glob để xóa releases. Cleanup tool phải nhận exact release key,
in inventory/size, yêu cầu backup manifest khớp và refuse active release. Với
local files, ưu tiên move-to-trash/archive trước permanent deletion.

Về lâu dài, local artifacts dùng content-addressed layout để các release không
copy lại `content.csv` 42 MB nhiều lần:

```text
var/artifacts/blobs/<sha256>
var/artifacts/releases/<release_key>/manifest.json
var/backups/postgres/
var/backups/neo4j/
var/backups/qdrant/
```

Thư mục này gitignored, không nằm trong Docker build context và được mount chỉ
vào corpus/backup jobs, không mount vào API/web containers.

### 8.9 Full Docker target

Hai deployment profiles dùng cùng images:

```text
local-full
  web + api + migrate + postgres + qdrant + neo4j
  optional corpus-worker/backup jobs

managed-production
  web + api + one-shot migrate
  external Supabase PostgreSQL + Qdrant Cloud + Neo4j Aura + Langfuse/OpenAI
```

Không chạy một compose production duy nhất chứa hard-coded cloud secrets. Base
Compose mô tả app; override/profile chọn local infrastructure hoặc managed
endpoints.

| Service | Trách nhiệm | Persistent state |
|---|---|---|
| `web` | Next.js standalone UI | none |
| `api` | FastAPI runtime read path | none; cache không mang correctness |
| `migrate` | one-shot Alembic migration, advisory lock | PostgreSQL only |
| `postgres` | local canonical/lexical/conversation store | `postgres_data` |
| `qdrant` | local semantic projection | `qdrant_data` |
| `neo4j` | local graph projection | `neo4j_data`, logs optional |
| `corpus-worker` | profile-only build/embed/import/publish | `artifact_data` |
| `backup` | profile/cron-only exact-target backups | `backup_data` |

Startup dependency:

```text
postgres healthy → migrate completed → api ready
qdrant healthy ────────────────────────┤
neo4j healthy ─────────────────────────┘
api healthy → web
```

`depends_on` chỉ hỗ trợ startup local; production vẫn cần retry/backoff vì
dependency có thể mất sau startup. `/health` là liveness; `/ready` kiểm tra
active release + required providers. Docker healthcheck của API dùng `/health`
để tránh restart loop khi cloud dependency chập chờn; load balancer dùng
`/ready` để ngừng route traffic khi release không usable.

### 8.10 Docker image/build requirements

Backend:

- Split `requirements-runtime`, `requirements-pipeline`, `requirements-dev` và
  lock hashes/versions; production image không chứa pytest, ruff, NumPy/pipeline
  nếu API không dùng chúng.
- Builder tạo wheels hoặc virtualenv ở `/opt/venv`; không copy packages vào
  `/root/.local` rồi chạy bằng non-root user.
- Copy allowlist `src/` và runtime files, không `COPY . .`.
- Non-root UID/GID cố định, read-only root filesystem, writable `/tmp` tmpfs.
- Pin Python 3.11 patch/base digest; tạo SBOM và vulnerability scan trong CI.
- `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, graceful SIGTERM và explicit
  Uvicorn worker count.

Frontend:

- Thêm `web/Dockerfile` multi-stage với locked `npm ci`.
- Bật Next `output: "standalone"`; runtime image chỉ copy standalone/static/public.
- Dùng Node LTS compatible với Next 16, pin exact image/digest.
- `NEXT_PUBLIC_*` là build-time public config; server secrets không được dùng
  tiền tố này hoặc bake vào JS bundle.

Build context:

- `.dockerignore` phải loại `.venv*`, `data/clean/`, `database/backups/`,
  `eval/results/`, `.ai-log/`, `.next/`, node modules, credentials và local
  artifact volumes.
- Raw corpus, embedding `.npy`, Neo4j exports và backup tuyệt đối không nằm trong
  API/web image layers.
- CI ghi build-context size và fail nếu vượt budget; target ban đầu <50 MB cho
  backend context, không phải hơn 3 GB như workspace hiện có thể gửi.

### 8.11 Compose networking, storage và secrets

- Ba networks: `edge` (web/API), `app` (API/data), `data` internal.
- Production chỉ expose web/reverse proxy; database ports không publish ra host.
- Local dev nếu cần DB UI chỉ bind `127.0.0.1`, không `0.0.0.0`.
- Named volumes tách PostgreSQL/Qdrant/Neo4j/artifacts/backups; không dùng một
  shared writable project bind mount.
- Pin Qdrant và Neo4j major/minor; không dùng floating `latest`.
- Đặt CPU/memory/file-descriptor limits và Neo4j heap/page-cache theo host.
- Qdrant local bật API key; Neo4j không fallback `change-me` ngoài disposable
  test profile.
- Secrets dùng platform secrets/Docker secrets hoặc mounted files. Thêm `_FILE`
  support cho database password, OpenAI, Qdrant, Neo4j và Langfuse keys.
- Không lặp secret vừa trong `env_file` vừa trong `environment`; giá trị secret
  không xuất hiện trong `docker compose config`, image history hoặc logs.

Pool budget phải tính theo replica:

```text
(DB_POOL_SIZE + DB_MAX_OVERFLOW) × api_replicas + worker_connections
    <= provider connection budget - migration/admin reserve
```

API không giữ DB session khi chờ OpenAI/Qdrant. Corpus worker có pool riêng và
không dùng cùng quota/runtime priority.

### 8.12 Environment contract

Chuẩn hóa files:

| File | Vai trò |
|---|---|
| `.env.example` | đầy đủ mọi variable được support, chỉ chứa placeholder, gồm AI Log |
| `.env.local.example` | endpoint host-local, không secret |
| `.env.docker.example` | service DNS như `postgres`, `qdrant`, `neo4j` |
| `.env` | credential thật, gitignored, không bake vào image |
| `secrets/*.txt` | optional local Docker secret files, gitignored |

Tách quyền/URL:

- `RUNTIME_DATABASE_URL`: read-only corpus + owner-scoped app access.
- `PIPELINE_DATABASE_URL`: stage/publish release, không DDL.
- `MIGRATION_DATABASE_URL`: DDL owner, chỉ mount vào migrate job.
- `QDRANT_URL/API_KEY`, `NEO4J_URI/USERNAME/PASSWORD/DATABASE`.
- OpenAI embedding/answer model configuration tách rõ nếu dùng key/model khác.
- `LANGFUSE_*`, `AI_LOG_*` và Firebase config phải còn nguyên trong example;
  không được “dọn” mất AI Log.
- Xóa `CHROMA_PERSIST_DIR`, LangSmith và pgvector variables khi code search xác
  nhận không production consumer.
- Xóa `SUPABASE_URL/ANON_KEY` khỏi runtime contract nếu vẫn không có consumer;
  giữ chúng trong profile riêng nếu frontend/API sau này dùng Supabase REST.

`.env.example` không chứa project/cluster endpoint thật. Các credentials từng
được chia sẻ qua chat hoặc xuất hiện ngoài secret manager phải được rotate trước
public deployment; việc copy chúng sang Docker secret không làm key cũ hết lộ.

### 8.13 Full-stack Docker verification matrix

CI/local gate trước khi gọi “Docker-ready”:

1. `docker compose config` pass cho local-full và managed-production profiles.
2. Backend/frontend images build reproducibly từ clean checkout; không secret,
   data artifact hoặc backup xuất hiện trong layer/SBOM.
3. Fresh empty volumes: Postgres healthy → migration một lần → fixture seed →
   API ready.
4. Re-run migrate không đổi schema/data; chạy hai migrate jobs đồng thời vẫn
   chỉ một job thực thi nhờ advisory lock.
5. Exact, lexical, semantic và graph integration tests pass qua service DNS.
6. Restart từng container không mất volume data; restart API không mất
   conversation/release state.
7. Kill Qdrant/Neo4j: API degraded đúng policy, không mixed evidence, không
   restart loop.
8. Rotate secret/recreate API container không rebuild image.
9. Backup active release, destroy disposable volumes, restore và pass parity.
10. Publish candidate, rollback previous release và pass cùng release/hash gates.
11. Load test 20 concurrent requests; pool không starvation, queue/backpressure
    hoạt động và memory không tăng không giới hạn.
12. Image/context/volume sizes được report; retention job refuse active target.

### 8.14 Những việc không nên làm

- Không đưa canonical legal corpus hoặc conversations vào Qdrant.
- Không xóa Neo4j cũ/local snapshots chỉ vì “trông thừa” trước restore gate.
- Không chạy migrations ở mỗi API replica startup.
- Không cho runtime container quyền DDL/service-role.
- Không bake corpus, `.env`, vector artifact hoặc database dump vào image.
- Không dùng mutable Qdrant collection cho nhiều releases.
- Không giữ cả pgvector và Qdrant production path “phòng khi cần”; rollback dùng
  versioned Qdrant collection/artifact, không dùng implementation thứ hai.
- Không self-host full Supabase suite khi app chỉ cần PostgreSQL.
- Không re-embed chỉ để đổi relational schema; reuse artifact nếu ID/hash parity
  còn nguyên.

## 9. Những vấn đề kiến trúc/code còn lại

### P0 — correctness/release blockers

- [Resolved 2026-08-21] Supabase release manifest trả expected Qdrant point count bằng 0.
- [Resolved 2026-08-21] Supabase `collection_name` trỏ pgvector legacy; runtime Qdrant dùng một
  physical collection khác.
- [Resolved 2026-08-21] 14.393 semantic passages chưa có input hash tương ứng trong Supabase; hash
  guard online vì vậy chưa enforce được parity.
- Chưa có rollback release đồng thời ở cả Supabase/Qdrant/Neo4j; không được prune
  release cũ cuối cùng trước khi restore/rollback drill pass.
- Schema có hai authorities và hai vector paths; mọi migration tiếp theo phải
  dừng runtime DDL và chọn một authority trước.
- Credentials đã từng được truyền ngoài secret manager cần rotate trước public
  deployment/Docker production.
- Firebase Admin private key đã từng xuất hiện ngoài secret manager phải revoke,
  tạo key mới và chỉ mount qua secret store; không chép credential vào
  `.env.example`, `PLAN.md`, image, build log hoặc Vercel.
- Chưa có current full end-to-end eval sau các fix mới.
- `verify_evidence_node` vẫn kiểm tra provenance/span; guardrail đã thêm claim →
  citation lexical audit nhưng semantic entailment verifier vẫn còn mở.
- [Resolved 2026-08-21] Sibling legal-unit expansion đang kích hoạt từ heuristic `a)`–`h)`; đã thêm
  intent gate cho câu hỏi liệt kê/phạm vi để tránh over-expansion.
- [Resolved 2026-08-21] Legal-unit exact path đang early-return trước relational/temporal retrieval;
  chỉ được fast-return với simple lookup. Câu hỏi “Điều X sửa đổi/hiệu lực/so
  sánh với văn bản nào” phải tiếp tục decomposition + hybrid/graph fusion.
- [Resolved 2026-08-21] Exact metadata formatter có thể xuất `legal_status` khi candidate chưa mang
  cờ/provenance `legal_status_verified`; mọi claim hiệu lực phải fail closed về
  nguồn trạng thái chính thức hoặc trả lời chưa đủ bằng chứng.
- [Resolved 2026-08-21] Citation list hiện gắn evidence cho toàn answer nhưng chưa map từng claim tới
  citation cụ thể; API giờ trả claim audit mapping.
- [Resolved 2026-08-21] Active-release/retrieval cache cần đảm bảo không trả mixed/stale release sau
  atomic cutover.
- Conversation memory không được phép trở thành evidence hoặc làm lẫn release
  giữa hai turns.

### P1 — data platform

- Tạo `ops/corpus/app` shadow schemas và Alembic explicit migrations.
- Chuyển release correctness fields khỏi opaque manifest JSON sang typed
  release/projection rows.
- Bỏ duplicate chunk IDs/indexes, empty payloads và pgvector columns theo
  expand/cutover/contract plan; không drop in-place trước shadow parity.
- Tạo generated FTS, typed document metadata và least-privilege runtime,
  pipeline, migration roles.
- Chuyển active hotfix thành immutable correction input/release mới; cấm drift
  giữa source artifact, PostgreSQL, Qdrant và Neo4j.
- Áp dụng active + one rollback retention và content-addressed local artifacts.

### P1 — latency/concurrency

- [Resolved 2026-08-21] Cache/reuse một `ChatOpenAI` client theo model/config.
- [Partial 2026-08-21] SSE/streaming từ FastAPI → Next.js đã có; raw provider token còn được buffer để claim safety.
- [x] Prewarm DB/Qdrant/Neo4j probes trong lifespan và readiness riêng khỏi
  request; coalesced 5-second probe cache giữ 100 concurrent readiness calls ở
  p95 137.94 ms tại `eval/results/local-readiness-load-100.json`.
- [Resolved 2026-08-21] Không giữ DB session khi chờ OpenAI/Qdrant.
- Gộp hydrate + legal-unit scope expansion thành một bounded query.
- [Resolved 2026-08-21] Thay character truncation bằng tokenizer-aware context packing, chỉ cắt ở
  ranh giới evidence và luôn bảo toàn citation span/provenance.
- [x] Tạo deterministic formatter cho metadata, legal-unit list và policy
  routes; bỏ LLM ở các route extractive rõ ràng.
- Thêm release-keyed answer cache cho public legal questions an toàn.
- Thiết lập circuit breaker và timeout budget từng provider.
- Deploy backend gần Supabase/Qdrant; đo DNS/TCP/TLS/server latency riêng.
- Thêm bounded semaphore/backpressure cho từng external provider và single-flight
  cho embedding/retrieval trùng nhau.
- [x] Không batch final generations của nhiều user trên interactive path; batch
  sub-query embeddings/Qdrant và offline workloads.

### P1 — eval

- [x] Tách exact benchmark khỏi semantic benchmark; xóa gate semantic-on-ID sai.
- [x] Chạy release-locked 292-case deterministic coverage: exact/graph 200,
      thematic semantic 80 và edge policy/no-answer/table 12 đều có evidence;
      human-adjudicated quality/RAGAS trên toàn 292 vẫn là gate riêng.
- Tạo human-adjudicated holdout tối thiểu 200 câu.
- Chạy ablation: lexical, semantic, +focus, +scope, +graph, +rerank.
- Theo dõi p50/p95/p99 theo stage trong Langfuse.

### P2 — product/security

- [Resolved 2026-08-21] Firebase token verification/profile routes, `/chat`/`/analyze` auth và frontend bearer refresh đã có.
- [x] API có in-memory/Redis rate limit, abuse body guard và per-user cost
  quota với kill-switch theo cửa sổ chi phí; local Redis path và unit tests pass.
- [Resolved 2026-08-21] Frontend gửi `chat_history` với giới hạn số messages/tổng payload.
- [Partial] Stable `conversation_id`/`turn_id`, server-side turn store,
  ownership/RLS, retention/delete and typed citation anchors đã có; multi-turn
  quality/isolation eval còn mở.
- [x] Langfuse `session_id` dùng conversation ID khi tracing JSON; stream span
  grouping now carries conversation/turn metadata.
- Admin authentication and review queue now use Firebase-backed role checks,
  PostgreSQL RLS and audit events; external production-admin browser rehearsal
  remains open.
- README nói invoice/OCR/payment guidance nhưng backend thực tế chưa có OCR
  workflow; `/analyze` chỉ dùng lại agent text.
- [x] SSE client disconnect closes the LangGraph event stream; provider-level
  cancellation is bounded by the upstream async iterator.
- [x] Admin decision audit trail is persisted in `review_audit_events`; data
  correction remains release-based and immutable.

### P2 — maintainability

- `src/graph_rag/*` còn một compatibility shim cũ; example node/tool và unused
  LLM integration đã được xóa sau import scan.
- Data pipeline/docs/requirements đã chuyển khỏi pgvector callers và DDL;
  legacy vector metadata columns chỉ còn trong rollback window.
- `GraphRepository` đang chứa Supabase read model lẫn optional graph adapter;
  tên và boundary chưa rõ.
- LangGraph hiện tuyến tính; `extract_entities_node` chỉ bọc nguyên query, chưa
  phải entity extraction.
- Dependencies nguồn vẫn dùng range để compile, nhưng runtime/pipeline/dev/migrate
  lockfile có hash đã được tạo; Langfuse availability vẫn cần external smoke.
- Local Python là 3.14 trong khi Docker/CI là 3.11; cần test matrix hoặc chuẩn hóa.
- README ghi Next.js 14 nhưng frontend dùng Next 16; React runtime 19 trong khi
  `@types/react` đang 18.
- Workspace local khoảng 10 GB và giữ nhiều release trung gian `v2`–`v31`.
  Cần retention policy: giữ active release, một rollback release, final source
  và embedding artifact; archive phần còn lại theo checksum trước khi xóa.

### P2 — frontend/deployment

- Frontend đã pass TypeScript và production build; ESLint pass với 4 warning.
  Các lệnh đã vào CI; `npm audit` hiện 0 vulnerability. Warning và browser
  staging smoke vẫn cần xử lý trước production.
- `render.yaml` và `web/vercel.json` đã được version hóa; staging domain,
  preview protection và end-to-end browser smoke giữa Vercel/Render vẫn mở.
- Docker healthcheck chỉ dùng liveness, hợp lý; current images có dependency
  lock, CycloneDX SBOM và SARIF evidence tại `eval/results/`.
- `docker-compose.yml` hiện mô tả Qdrant/Langfuse boundary; không còn pgvector
  caller trong source/runtime.
- Dockerfile đã dùng `/opt/venv`, allowlist copy, non-root và read-only runtime.
- Docker context hiện deny-list `data/clean/`, backups, `outsource/`, eval và
  frontend artifacts; local context-size/SBOM contract pass, CI attestation
  ownership vẫn cần external pipeline verification.
- Frontend image, one-shot migrate service, local PostgreSQL/Qdrant/Neo4j/Redis
  profile và internal data network đã có; secret mounts, SBOM và full
  restart/restore drill vẫn mở.

## 10. Audit toàn bộ `outsource/` và quyết định tích hợp

### 10.1 Phạm vi, phương pháp và source lock

Đã inventory/classify toàn bộ khoảng 2.305 file ngoài nested `.git`, tổng khoảng
297 MiB. Source, config, schema, prompt, docs, test, CI và deploy path được
parse/index rồi deep-read theo critical path; JSON/JSONL/YAML được validate;
binary/model/index/generated output được nhận diện bằng type, size, provenance và
consumer thay vì xem như source code. Bốn PDF đã được trích và đọc đủ 85/85
trang; `OMD-GraphRAG.md` đủ 77/77 dòng.

| Artifact | Snapshot đã audit | Quy mô có ý nghĩa | Vai trò tham khảo |
|---|---|---:|---|
| HippoRAG | `c617143` | 97 source/doc/data file | recognition memory + PPR |
| LegalGraphRAG | `a3c9c30` | 65 file | typed legal graph + Auditor |
| LightRAG | `300d9df1` | 1.043 tracked file | durable ingestion + storage/query patterns |
| MemGraphRAG | `cd6fabd` | 66 code/doc/config file | schema/fact/passage memory |
| Microsoft GraphRAG | `6dad6d2b` | 906 file | BYOG, token packing, global/DRIFT patterns |
| Youtu-GraphRAG | `d982b5a` | 63 file | schema routing + parallel multi-path retrieval |
| `OMD-GraphRAG.md` | local artifact | 77 dòng | secondary hypothesis only |
| 4 research PDF | local artifact | 85 trang | agentic RAG, RAG security, table RAG, LegalGraphRAG |

Phần code Python của Hippo/Legal/Mem/Youtu gồm 173 file/25.420 LOC và parse AST
173/173. LightRAG có 480 Python test file nhưng nhiều integration dependency;
Microsoft GraphRAG có 408 test declaration được discover nhưng query
orchestration chỉ có ba test discover được. Ba repo Legal/Mem/Youtu không có
unit-test suite thực. Vì vậy số lượng file/test không được diễn giải thành
production maturity hay coverage.

Các cross-check bổ sung: 6/6 checksum artifact LegalGraphRAG pass; static Ruff
trên bốn repo nhỏ báo 3.917 issue nên code không thể được vendor như production
library; Hippo unit test không collect trong môi trường chung vì thiếu stack
`transformers`; Youtu `ConfigManager.to_dict()` tái hiện `AttributeError` do đọc
field chưa tồn tại. Đây là audit signal, không phải lỗi cần sửa trong upstream.

`outsource/` là evidence workspace, không phải dependency/runtime source:

- Không commit nested repository/binary/model/data khoảng 297 MiB vào product
  repo và không đưa chúng vào Docker context.
- Trước khi cleanup local, tạo một manifest nhỏ gồm upstream URL, commit, license,
  SHA-256 của năm research artifact và ngày audit. Việc copy code, nếu có, phải
  qua license review; ưu tiên viết adapter/algorithm nhỏ theo pattern.
- Benchmark/claim trong README/paper chỉ sinh hypothesis. MediPay chỉ promote
  thay đổi sau release-locked ablation trên corpus tiếng Việt hiện tại.
- Không dùng generated demo/output của upstream làm expected answer hoặc gold
  data cho BHYT.

### 10.2 Ma trận quyết định theo từng dự án

| Nguồn | Pattern nên lấy | Không được mang nguyên | Quyết định |
|---|---|---|---|
| LightRAG | DB-truth ingestion state machine, bounded staged queue, commit marker, recovery anchor/keyed lock, batch embedding/graph reads, strict storage contract, body/admission limit, rerank fail-open | storage matrix lớn, process-local coordination, sync Qdrant trong async, per-call clients, round-robin fusion, file-only citation, custom auth/WebUI, runtime install | Áp dụng có chọn lọc vào offline pipeline và API hardening |
| Microsoft GraphRAG | content-hash TextUnit, workflow registry, token-aware whole-block context, embedding batch/cost metrics, cache namespace, BYOG/community hypothesis | full LLM/OpenIE indexer, English FastGraphRAG, LanceDB, title-only incremental update, global/DRIFT mặc định, busy-loop rate limiter, unsupported unified UI | Giữ runtime hiện tại; community/global chỉ P2 gated experiment |
| HippoRAG | fact recognition trước propagation, dense passage fallback, bounded PPR để tăng recall multi-hop | Pickle/Parquet/igraph runtime, unsafe `eval()`, GPU-heavy alpha stack, cache/collection không release scoped, config có thể log secret | Viết PoC nhỏ trên approved release graph; không port storage/runtime |
| LegalGraphRAG | Fact–Ontology–Rule idea, Researcher/Auditor checklist, traceable-correct metric, claim-by-statute verification | criminal labels/corpus, graph UUID mismatch, unused retrieved facts, O(N²) KNN, hard-coded dropping low-number laws, Pickle/`eval()`, multi-agent 46 s/query | Viết lại typed BHYT ontology + Auditor trên provenance hiện có |
| MemGraphRAG | typed schema/fact/passage layers và bidirectional passage provenance | auto-evolve/filter rare schema, LLM modify/delete fact, simplistic conflict handling, local JSON/GraphML/Pickle, undirected PPR mặc định | Ontology controlled; conflict chỉ là offline QA flag |
| Youtu-GraphRAG | bounded parallel query decomposition, schema-aware routing, multi-path recall/TreeComm hypothesis | API không auth/path traversal/delete risk, wildcard CORS, random untrained projection, exact-string entity identity, weak cache fingerprint, auto-schema | Chỉ A/B algorithm; tuyệt đối không dùng API/storage/deploy |
| OMD note | ontology type-checking và router là hypothesis | các con số F1 không có source/code/raw result kiểm chứng | Không dùng làm evidence; chỉ ablation backlog |

Chi tiết quyết định then chốt:

**LightRAG.** Pattern ingestion đáng học nhất là `PENDING` trước mutation, flush
canonical trước status, durable recovery anchor, sorted/keyed merge lock và chỉ
`PROCESSED` sau khi graph/vector/KV đã flush. MediPay chuyển thành job state
machine ở PostgreSQL với lease/idempotency; không dùng `multiprocessing.Manager`
lock vì không đúng khi Render có nhiều replica. Retrieval của MediPay hiện tốt
hơn ở lexical + dense song song và weighted RRF; không thay bằng các branch tuần
tự/round-robin của LightRAG.

**Microsoft GraphRAG.** Full standard indexer cần ít nhất một extraction và mặc
định một gleaning call cho mỗi TextUnit; nếu áp lên 37.170 passage thì riêng bước
này đã xấp xỉ 74.340 completion call, chưa tính summaries/reports. Nó vừa đắt,
vừa có thể sai identity/predicate/hiệu lực pháp lý. BYOG chỉ đáng thử sau này:
chạy community trên graph đã curated, summary là derived navigation artifact và
answer vẫn re-retrieve Supabase. DRIFT mặc định có fan-out lớn nên chỉ là async
research job depth 1, K 3–5, có deadline/token/cost/cancel.

**HippoRAG.** Recognition memory + PPR có giả thuyết tốt cho câu multi-hop,
nhưng repo không kèm machine-readable accuracy/latency result; chỉ có benchmark
corpus và OpenIE dumps. PoC phải dùng Qdrant/Neo4j hiện tại, filter `release_id`,
approved evidence và hợp nhất bằng weighted RRF. Cấm `eval()` output/persisted
fact và untrusted Pickle.

**LegalGraphRAG.** Paper `2605.28120v1` báo aggregate 49,5, traceable-correct
38,1% và ablation Auditor giảm 3,4 điểm; đổi lại online trung bình 46,1 giây và
10.664 token/query. Đây là bằng chứng rằng Auditor/traceability đáng thử, không
phải lý do port multi-agent runtime. Release code còn mismatch graph UUID với
law number và không thật sự dùng retrieved facts trong final judge, nên mọi
pattern phải được viết lại và test trên schema/provenance MediPay.

**MemGraphRAG.** Không cho LLM `kept/modified/discarded` trực tiếp trên fact pháp
lý và không lọc 20% schema hiếm; ngoại lệ hiếm thường là phần quan trọng nhất.
Conflict detection chỉ tạo review item gồm hai source spans, temporal/jurisdiction
context và reason; human/release pipeline mới quyết định correction.

**Youtu-GraphRAG.** Parallel decomposition và multi-path retrieval chỉ nên chạy
với max sub-query/step/deadline và shared candidate budget. Không lấy server demo:
nó có wildcard CORS với credentials, upload path traversal và DELETE path dựa
trên untrusted dataset name. Cache của MediPay phải kiểm cả content/edge/model/
release fingerprint, không chỉ node-ID set và dimension.

### 10.3 Quyết định từ bốn paper và OMD

| Tài liệu | Kết luận có thể dùng | Hành động trong MediPay |
|---|---|---|
| `2603.07379v1`, *SoK: Agentic RAG* | Decomposition song song có lợi; retrieve-reflect/IRCoT tuần tự làm khuếch đại latency/token. Cần đo cả trajectory, termination và tool correctness | Planner finite/bounded, max-step/fan-out/cost, early-stop và trajectory audit; không “agentic” mặc định |
| `2604.08304v3`, *Securing RAG* | Threat model SLOT: poisoning, retrieval manipulation, retrieved-context exploitation, extraction; phòng thủ từ provenance đến access control | Security P0 ở §10.4; red-team cả upstream và downstream |
| `2605.01495v1`, *FT-RAG* | Cell-level Subject–Attribute–Temporal index giữ exact value/provenance; paper báo exact-value recall 54,5% so 33,9% baseline nhưng không báo latency | P1 PoC trên `document_tables/table_cells`, không tạo store mới; cite cell/source span |
| `2605.28120v1`, *LegalGraphRAG* | Typed ontology + Auditor tăng traceability nhưng multi-agent rất chậm và code artifact có lỗi | Lấy checklist/metric, bỏ criminal ontology và orchestration nguyên khối |
| `OMD-GraphRAG.md` | Secondary note nói ontology/routing/fusion giúp F1 nhưng không có primary evidence | Chỉ giữ hypothesis; không đưa số vào release claim |

Cell-level SAT PoC cho bảng quyền lợi:

```text
cell_unit = subject + effective_time + attribute + value + condition
          + table_id + row/column + document/unit/span + release/hash
```

Dual traversal lấy anchor (đối tượng/mốc thời gian) và attribute (mức hưởng,
điều kiện), mở rộng hàng/cột lân cận có giới hạn rồi hydrate narrative/legal unit.
Không embed mọi cell mặc định; benchmark exact value, unit, row coverage, OCR/
table error và latency trước. PostgreSQL table index vẫn là truth, graph/vector
chỉ là optional projection.

### 10.4 Threat model RAG bắt buộc trước public deploy

Áp dụng SLOT thành release/security gates:

| Surface | Threat | Control bắt buộc | Test |
|---|---|---|---|
| S1 knowledge | poisoned/forged corpus, compromised hotfix | official-source allowlist, checksum/signature, reviewer, immutable correction/release, remediation lineage | inject forged doc/release; publish phải fail |
| S2 retrieval | malicious metadata/embedding, rank manipulation | typed filters, release/model fingerprint, lexical+dense disagreement signals, diversity, projection parity | poisoned high-score chunk không được thành claim |
| S3 context | retrieved prompt injection/tool instruction | data/instruction isolation, quoting/escaping, tool allowlist, no tool from evidence text, claim verifier | direct/indirect injection suite S1–S4 |
| S4 extraction | corpus/PII/system prompt exfiltration | auth/RBAC, selective disclosure, output filter, rate/quota, audit | unauthorized enumeration/exfiltration T1/T2 |
| Memory | cross-user poisoning/leakage | owner/RLS, bounded typed memory, never evidence, re-retrieve per turn | cross-user/session/release isolation |
| Operations | stale/mixed cache or rollback failure | full fingerprint cache key, parity readiness, active/previous drill | cutover mid-request, stale projection/cache |

Retrieved text luôn được gắn nhãn untrusted data; nó không thể cấp instruction,
thay đổi tool/route, vô hiệu policy hay yêu cầu tiết lộ hidden context. Red-team
phải bao gồm adaptive multi-turn và cross-surface attack, không chỉ một prompt
injection đơn giản.

### 10.5 Kết luận audit và thứ tự tiếp nhận pattern

Baseline giữ nguyên **Seed → Expand → Re-retrieve → Verify** với PostgreSQL,
Qdrant và Neo4j. Không thêm framework dependency nào trong production image.

1. **P0:** release-scoped cache/index fingerprint; Firebase/auth/admission;
   RAG threat model; sửa legal-unit early-return và verified-status provenance.
2. **P1:** durable corpus state machine; token-aware packer; typed BHYT ontology;
   Auditor/claim checklist; cell-level SAT table PoC.
3. **P2:** multilingual reranker, bounded decomposition, recognition/PPR và BYOG
   community/global theo thứ tự ablation ở §11.5.
4. **Reject:** auto-evolving ontology, drop rare law/schema, LLM mutate/delete
   canonical facts, untrusted Pickle/`eval()`, upstream demo API và multi-agent
   46-second path làm default.

### 10.6 Evidence anchors để thực thi mà không phải audit lại

| Quyết định | Evidence chính |
|---|---|
| Durable ingestion/commit/recovery | `outsource/LightRAG/lightrag/pipeline.py:1087-1568,2539-2792,5385-5567`; `lightrag/operate.py:3627-3869` |
| Batch/token/rerank/admission | `outsource/LightRAG/lightrag/operate.py:5071-5124,5887`; `lightrag/utils.py:5768-6006,6669-6718`; `lightrag/api/body_limit_middleware.py:35-178` |
| BYOG/community/global/DRIFT tradeoff | `outsource/graphrag/docs/index/byog.md:5-68`; `packages/graphrag/graphrag/query/structured_search/global_search/search.py:142-431`; `config/defaults.py:88-115` |
| GraphRAG incremental/cache/rate anti-patterns | `outsource/graphrag/packages/graphrag/graphrag/index/update/incremental_index.py:29-58`; `packages/graphrag-llm/graphrag_llm/rate_limit/sliding_window_rate_limiter.py:77-135` |
| Recognition/PPR | `outsource/HippoRAG/src/hipporag/HippoRAG.py:1544-1749`; unsafe parsing tại `:1691-1694` |
| Legal ontology/Auditor | `outsource/LegalGraphRAG/core/graph_construct/feature_graph.py:397-552`; `core/judge/judge_crime.py:41`; paper `2605.28120v1`, p.4–8,21 |
| Memory/conflict risk | `outsource/MemGraphRAG/code/src/MemGraphRAG.py:358-756,2203-2299` |
| Youtu decomposition/API risk | `outsource/youtu-graphrag/models/retriever/enhanced_kt_retriever.py:761-793`; `backend.py:269,1085-1114` |
| Current early-return/status/context bugs | `src/services/chat.py:168-199,237-276,399-411`; `src/agents/nodes/graphrag_nodes.py:54-69` |
| Security/table/agentic research | `2604.08304v3`, p.3,7–12; `2605.01495v1`, p.3–10; `2603.07379v1`, p.5,8–21 |

Line anchors thuộc đúng commits ở §10.1; nếu upstream snapshot đổi thì manifest
và audit decision phải được refresh trước khi dùng chúng để implement.

## 11. Kiến trúc đích: nhanh nhất trong giới hạn đúng và kiểm chứng được

### 11.1 Luồng online chuẩn

Không thay runtime bằng LightRAG, Microsoft GraphRAG hay một framework mới.
Giữ modular monolith hiện tại và làm rõ một pipeline thích nghi:

```text
Vercel browser
  → Firebase ID token + request ID
  → Render admission: body limit → auth → UID/IP quota → deadline
  → normalize + deterministic policy/status/entity/intent planner
  ├─ policy/refusal                          → deterministic response
  ├─ simple exact metadata/legal unit       → PostgreSQL → formatter
  └─ evidence route
       ├─ lexical/PageIndex/filter search ─┐
       ├─ query embedding → Qdrant dense ──┼─ parallel, bounded deadline
       └─ approved Neo4j seed expansion ───┘  only for relational/temporal
            → weighted RRF
            → optional reranker
            → diversity + legal-unit/table coverage selector
            → canonical PostgreSQL hydration
            → token-aware whole-evidence packing
            → deterministic extractive answer OR streaming LLM
            → claim ↔ evidence verification
            → citations + release fingerprint
```

Các invariant không được thương lượng:

1. PostgreSQL/Supabase là legal truth; Qdrant và Neo4j chỉ là projection có thể
   rebuild theo `release_id`.
2. Graph dùng để tìm đường, không tự trở thành bằng chứng. Mọi node/edge được
   dùng cho answer phải re-retrieve về passage canonical.
3. Summary, hypothesis, conversation memory và community report chỉ là routing
   hint; không bao giờ là legal evidence.
4. Câu exact/extractive không gọi embedding hoặc LLM nếu SQL/formatter đã đủ.
5. Câu trạng thái pháp lý phải có `verified_status` cùng nguồn/ngày kiểm chứng;
   thiếu thì abstain thay vì suy đoán.
6. Mỗi cache, trace, citation và answer phải mang release fingerprint; cutover
   không bao giờ được trộn hai release trong cùng request/turn.

### 11.2 Router theo loại câu hỏi

| Route | Dấu hiệu | Retrieval/generation | Mục tiêu p95 warm |
|---|---|---|---:|
| Policy/safety | prompt injection, ngoài phạm vi, medical emergency | rule engine, không retrieval/LLM nếu đã quyết định được | ≤300 ms |
| Exact document | số/ký hiệu, cơ quan, ngày, tiêu đề | indexed metadata SQL + deterministic formatter | ≤700 ms |
| Simple legal unit | Điều/Khoản/Điểm rõ ràng | exact resolver + sibling scope có intent gate | ≤1,2 s total |
| Table/list/numeric | “bao nhiêu”, danh mục a–h, quyền lợi theo hàng | PageIndex + cell/legal-unit coverage; formatter khi đủ | ≤2,5 s total |
| Hybrid topical | câu hỏi nội dung thông thường | lexical và dense song song → RRF → optional rerank | retrieval ≤1,5 s; TTFT ≤2,5 s |
| Temporal/relational | hiệu lực, thay thế, dẫn chiếu, so sánh | hybrid seeds → approved graph bounded → canonical re-retrieval | total ≤8 s |
| Thematic/global | tổng hợp nhiều văn bản/toàn corpus | baseline hybrid trước; community/global chỉ là P2 experiment | interactive ≤10 s hoặc chuyển async |
| Deep research | nhiều nhánh/multi-hop mở | async job, depth/fan-out/token/cost cap, progress + cancel | ≤20 s mặc định |
| No evidence | không đạt evidence threshold | reasoned abstention + gợi ý làm rõ | ≤2 s |

Legal-unit fast-return chỉ được dùng khi planner xác nhận `simple_lookup=true`.
Nếu câu hỏi có intent hiệu lực, sửa đổi, đối chiếu, nhiều mốc thời gian hoặc
nhiều văn bản, exact result chỉ là seed và pipeline phải tiếp tục fusion.

### 11.3 Tối đa hóa accuracy

Thứ tự cải thiện phải dựa trên ablation, không bật mọi kỹ thuật cùng lúc:

1. **Query normalization có kiểm soát**: số/ký hiệu, alias cơ quan, Unicode,
   Điều/Khoản/Điểm, địa phương và mốc thời gian; ontology/predicate được version
   hóa, không để LLM tự sửa schema.
2. **Typed filter trước vector**: `release_id`, `answer_ready`, jurisdiction,
   document type, issuing body, temporal validity và official-source status.
3. **Candidate generation bổ sung nhau**: exact/lexical bảo vệ identifier;
   dense bảo vệ paraphrase; graph chỉ mở rộng intent phù hợp.
4. **Candidate pool 3–5× rồi mới lọc**: weighted RRF giữ source score/rank;
   optional multilingual cross-encoder rerank top 20–30 xuống 6–10.
5. **Coverage selector**: phạt duplicate, bảo toàn đủ sibling a–h, các bên so
   sánh, table rows và temporal evidence; không chỉ chọn top score độc lập.
6. **Token-aware packer**: tính đúng tokenizer, reserve output tokens, chỉ cắt
   ở ranh giới evidence, giữ nguyên citation span/hash.
7. **Claim-first rendering**: `claim_id → evidence_id → release/document/unit/
   span/hash → entailed|partial|unsupported`; drop/downgrade unsupported claim.
8. **High-risk verifier**: trạng thái hiệu lực, mức chi trả, điều kiện loại trừ
   và số tiền phải có deterministic/second-pass verification; không stream phần
   kết luận trước khi pass.
9. **Answer mode thích nghi**: deterministic formatter cho exact/list/table;
   concise LLM cho synthesis; abstain rõ lý do khi evidence không đủ.

Community/Leiden report theo Microsoft BYOG chỉ được làm P2 experiment cho
thematic toàn corpus. Nó không phải baseline vì graph serving hiện quá thưa nếu
chỉ dùng approved edge, còn dùng toàn bộ audit edge sẽ làm giảm evidence safety.
Chỉ promote khi tăng thematic completeness và claim-citation score trên held-out
set trong cùng latency/cost budget; answer vẫn hydrate/cite canonical passage.

### 11.4 Tối đa hóa latency và throughput

- Khởi tạo một lần trong lifespan: async SQL engine/pool, Qdrant client, Neo4j
  driver, OpenAI client/model wrapper, Langfuse client và shared HTTP transport.
- Không giữ SQL session/transaction trong lúc chờ embedding, Qdrant, Neo4j hoặc
  generation. Hydrate theo hai bounded DB phases trước/sau remote calls.
- Dùng `TaskGroup`/structured concurrency cho branch độc lập; timeout budget,
  semaphore và circuit breaker riêng cho từng provider; cancel xuống tận provider
  khi browser disconnect.
- Batch sub-query embeddings và multi-ID reads trong cùng request; loại N+1
  graph/community reads. Không batch final answer của người dùng khác nhau trên
  interactive path.
- Prewarm connections và một query vô hại khi startup; đo cold và warm riêng.
- SSE phải gửi metadata/heartbeat, evidence-ready event, token event, citations,
  final/error; đo TTFT ở browser, API và model. High-risk answer có thể buffer
  đến khi verifier pass.
- Context/output budget theo route; concise by default. Exact/list formatter
  không phải trả chi phí generation.
- Replica đầu tiên dùng một async process để tránh nhân pool/client. Chỉ scale
  ngang sau khi tổng pool budget, rate limit, leases và cache semantics hoạt động
  đúng giữa replica.

Cache hierarchy:

```text
L1 immutable: release registry + document metadata
L2 bounded LRU: normalized-query embedding
L3 bounded LRU/optional Redis: retrieval bundle
L4 final answer: chỉ low-risk/public, sau claim verification
```

Key tối thiểu:

```text
release_fingerprint + normalized/resolved_query + intent +
retrieval_policy_hash + embedding_artifact_hash + reranker_version +
prompt_version + model_version
```

Cache có TTL/size bound, single-flight và negative TTL ngắn; không phải
correctness dependency. Cấm final-answer cache cho temporal/high-risk cho đến
khi invalidation và verifier được chứng minh.

### 11.5 Benchmark và luật promote

Gold set phải stratify, khóa bằng release hash và có tối thiểu 200–300 câu do
người review:

| Nhóm | Nội dung bắt buộc |
|---|---|
| Exact | số/ký hiệu, title, cơ quan, Điều/Khoản/Điểm, collision địa phương |
| Temporal | còn/hết hiệu lực, thay thế/bãi bỏ, câu hỏi “tại ngày X” |
| Relational | dẫn chiếu, sửa đổi, multi-hop, so sánh nhiều văn bản |
| Thematic | tổng hợp nhiều nguồn, câu broad/global |
| Structured | bảng, numeric, danh sách, cell và sibling coverage |
| Conversation | resolve “văn bản đó/khoản trên” nhưng retrieve lại evidence |
| Safety | no-answer, ambiguity, injection, PII, medical-advice boundary |
| Release/ops | cutover giữa request/turn, stale projection, rollback, timeout |

Metrics blocking:

- Retrieval: Recall@5/10/20, MRR, nDCG, document/legal-unit recall, graph-path
  precision, duplicate/diversity và table-cell coverage.
- Answer: claim-level citation precision/recall, entailment, completeness,
  contradiction, status-date accuracy, abstention false-positive/false-negative.
- Operations: p50/p95/p99 theo stage, TTFT, total latency, token/cost/query,
  cache hit, retry/error, cancellation, pool/queue saturation.
- Load: cold/warm, concurrency 1/10/25, sustained/burst và ba lần chạy để đo
  variance.

Experiment order:

1. Current baseline.
2. Metadata/temporal filters + aliases/query normalization.
3. Token-aware evidence packing.
4. Reranker + diversity/coverage selector.
5. Complex-query decomposition và bounded graph propagation/PPR.
6. Chỉ sau đó thử community/global và async deep analysis.

Một thay đổi chỉ được promote nếu không giảm exact/policy gate, cải thiện metric
mục tiêu trên held-out set có ý nghĩa, pass claim-citation/high-risk gate, và
không vượt p95/cost budget. Lưu toàn bộ config, model, prompt, release hash và
artifact; không chọn kết quả từ một run may mắn.

### 11.6 Bản đồ thay đổi runtime theo file/boundary

| Phạm vi hiện tại | Thay đổi dự kiến | Test bắt buộc |
|---|---|---|
| `src/api/routes.py` | protect chat/analyze, route SSE, stable errors, propagate user/conversation/request IDs | unauth/expired token, disconnect, provider timeout |
| `src/api/auth.py` | production fail-closed init, credential file/ADC, token refresh semantics, no private SDK internals | missing/malformed/revoked token/credential |
| `src/services/chat.py` | split DB phases, adaptive planner, exact fast-return gate, parallel branches, fingerprint caches | route matrix, mixed release, pool starvation |
| `src/services/llm.py` | lifespan singleton, streaming, output/deadline budget | reuse/close, TTFT, cancel, error mapping |
| `src/services/retrieval.py` | preserve weighted RRF, add optional rerank + diversity/table coverage | deterministic ranking, ablation, duplicate cap |
| `src/agents/nodes/graphrag_nodes.py` | token-aware evidence packer and structured claim render | no mid-evidence cut, overflow, citation span |
| `src/integrations/{qdrant,neo4j}.py` | batch, timeout, retry/circuit metrics, strict release filters | stale/wrong release, partial provider failure |
| `src/db/*` | repository boundary, short sessions, typed release/status reads | transaction duration, pool budget, RLS/roles |
| `src/models/*` | verified-status provenance, claim/evidence IDs, stream events | serialization/backward compatibility |
| `src/config.py` | env validation, per-provider budget, production safety checks | prod missing-secret fail closed |
| `web/lib/api.ts` | fresh bearer token, SSE parser, abort/retry-once contract | 401 refresh, malformed event, cancellation |
| `web/lib/auth-context.tsx` | token acquisition/refresh without long-lived stale string | expiry/sign-out/cross-tab behavior |
| `eval/` + `tests/` | stratified route/claim/trajectory/security/load gates | release/hash lock và artifact retention |

Giữ compatibility response hiện tại trong migration window. Version stream event
schema ngay từ đầu; client phải bỏ qua event type mới mà không crash.

## 12. Tinh gọn vật lý `database/` mà không mất khả năng phục hồi

### 12.1 Hiện trạng đo được

“Folder nặng” và “code phức tạp” là hai vấn đề khác nhau:

| Phạm vi | Hiện trạng local | Ý nghĩa |
|---|---:|---|
| Toàn `database/` | ~517 MiB | Chủ yếu là state/artifact ignored |
| File được Git theo dõi | ~840 KiB, 85 file | Không phải nguyên nhân chính làm repo nặng |
| `database/backups/` | ~463 MiB | Backup local, không được vào image/Git |
| `medical_docs_active.json` | ~49 MiB | Generated active artifact |
| `medical_relationships_active.json` | ~584 KiB | Generated graph artifact |
| `database/audit/results/` | ~3,6 MiB | Generated reports |
| `corpus/` + `pipeline/` | 64 tracked source/test files | Hai workflow chồng trách nhiệm, nhiều one-off script |
| `migrations/` | 8 SQL file | Có users migration nhưng `schema.sql` chưa phản ánh đầy đủ |

Do đó mục tiêu không phải xóa ồ ạt. Mục tiêu là: một migration authority, một
package pipeline có CLI/test, state sinh ra nằm ngoài source tree, và runtime
không import tooling offline.

### 12.2 Cấu trúc đích

```text
database/
  README.md                         # contract và cách migrate/restore
  migrations/
    alembic.ini
    env.py
    versions/                       # authority duy nhất, forward migration
  schema.generated.sql              # CI sinh từ migration head, không chỉnh tay
  contracts/
    release-manifest.schema.json
    graph-predicates.yaml
    canonical-fields.yaml
  fixtures/
    smoke/                           # nhỏ, synthetic, không chứa production data

packages/
  corpus_pipeline/
    pyproject.toml
    src/medipay_corpus/
      domain/                        # canonical, table, page-index, facet, manifest
      application/                   # build/validate/publish/rollback/audit/eval
      adapters/                      # Postgres/Qdrant/Neo4j/OpenAI/object storage
      cli.py
    tests/

deploy/
  local/                             # compose/profile và local service config

ops/
  runbooks/                          # backup/restore/cutover/incident/key rotation

var/                                 # gitignored + dockerignored
  artifacts/releases/<release-key>/
  audits/
  backups/
  reports/
```

Không tạo thêm một HTTP API trong pipeline package. `src/` là online API duy
nhất; corpus tooling chạy CLI/job với credential riêng.

### 12.3 Bản đồ di chuyển có kiểm soát

| Hiện tại | Đích | Quyết định |
|---|---|---|
| `pipeline/data_pipeline/{canonical,tables,page_index,facets}.py` | package `domain/` | Giữ logic thuần, chuẩn hóa type và invariant |
| `pipeline/data_pipeline/{storage,embedding}.py` | package `adapters/` | Interface nhỏ theo ba store, không storage matrix |
| `pipeline/scripts/*` | `medipay-corpus` CLI subcommands | Thay `PYTHONPATH`/`sys.path` hack bằng package install |
| `corpus/build_*`, `validate_*`, `qdrant_release.py` | application build/validate/publish | Idempotent job + release manifest |
| `corpus/apply_*_hotfix.py` | immutable correction input + rebuild | Cấm mutate active release sau khi replay parity đạt |
| `corpus/evaluate_*` | application `evaluate` hoặc `eval/tools/` | Một metric contract, output vào `var/reports` |
| `audit/audit_medical_corpus.py` | application `audit` | Machine-readable output, exit code blocking |
| `neo4j/scripts/import_relationships.py` | graph adapter/CLI | Import là projection build, không runtime path |
| `neo4j/docker-compose.yml` | `deploy/local/` | Deployment config không nằm trong DB source |
| `firebase/` | `web/` + central auth docs | Xóa duplicate sau consumer check; Admin ở backend/secret store |
| `pipeline/data_pipeline/api*.py` | deprecate/remove | Duplicate read-only FastAPI/retrieval stack; chỉ xóa sau no-consumer gate |
| `migrations/*.sql` + `schema.sql` | Alembic versions + generated snapshot | Một authority; users schema phải có trong fresh bootstrap |
| backup/JSON/audit output | `var/` + encrypted object storage | Không Git, không Docker context; có checksum/retention |

### 12.4 State machine và release contract

Học pattern bền vững từ LightRAG nhưng coordination phải ở PostgreSQL, không ở
process-local lock:

```text
discovered → canonicalized → chunked → embedded → graph_built
           → validated → staged → published → superseded
```

Mỗi transition có `job_id`, `release_id`, input/artifact hash, code/prompt/model
version, attempt, lease expiry, timestamps và stable error code. Job phải
idempotent; worker chết có thể lease/retry; chỉ `published` sau khi SQL, Qdrant
và Neo4j flush + parity pass. Mailbox/queue chỉ đánh thức worker, PostgreSQL mới
là source of truth.

Release registry typed cần giữ tối thiểu:

- canonical document/legal-unit/chunk counts và manifest hash;
- embedding model/dimensions/input hashes + Qdrant physical collection/count;
- graph projection version, node/edge/approved-edge counts;
- schema migration head, pipeline Git SHA và artifact locations;
- active/previous pointer, publish actor/time và rollback status.

Runtime role chỉ `SELECT` canonical/control-plane và đọc projections. Pipeline
role được ghi staging/projection. Migration role riêng có DDL; tuyệt đối không
đưa migration/service-role credential vào API container.

### 12.5 Trình tự tinh gọn và cổng xóa

1. Ghi import graph, owner và test coverage cho từng file hiện tại.
2. Tạo package + compatibility shims; chuyển pure modules trước, không đổi output.
3. Đưa CLI/import/CI/docs sang path mới; cấm thêm `sys.path` hack.
4. Chạy unit/contract test và rebuild disposable release từ source artifact.
5. So 100% IDs, counts, hashes, content, Qdrant payload và approved graph edges.
6. Restore backup rồi rehearsal publish/rollback trên môi trường disposable.
7. Chỉ sau no-consumer/import scan mới bỏ duplicate pipeline API/Firebase folder.
8. Chỉ sau deterministic replay mới archive hotfix script; giữ correction input.
9. Di chuyển local artifact sang `var/`/object storage bằng manifest + checksum;
   việc xóa vật lý là thao tác riêng, có xác nhận và retention policy.

Cleanup tool phải refuse active release, giữ tối thiểu active + một rollback,
dry-run mặc định và ghi audit. Không xóa backup cuối cùng cho đến khi restore
drill pass và object-store checksum đã xác minh.

Exit gate:

- Fresh database từ migration head có đầy đủ users/corpus/control-plane schema.
- `schema.generated.sql` diff bằng zero sau CI regeneration.
- Runtime không import `packages/corpus_pipeline` hay có DDL permission.
- Rebuild parity SQL/Qdrant/Neo4j và active/previous rollback = 100%.
- Tracked source tree không có production dump, embedding hoặc generated report.
- Clean Docker build context không chứa `data/`, `outsource/`, `var/` hay backup.

## 13. Render + Vercel production blueprint

### 13.1 Quyết định topology

```text
Firebase Auth ───────┐
                    ▼
User → Vercel Next.js → HTTPS/SSE → Render FastAPI
                                      ├─ Supabase PostgreSQL
                                      ├─ Qdrant Cloud
                                      ├─ Neo4j Aura
                                      ├─ OpenAI
                                      └─ Langfuse

GitHub Actions / protected operator
  → migration job → corpus/release job → parity gate → deploy/canary/rollback
```

- Vercel chỉ host `web/`; Render chỉ host stateless online API.
- Không đưa corpus pipeline, eval, `outsource/` hay local databases vào web/API
  image. Offline build/publish là protected job, không phải public endpoint.
- Không gắn persistent disk cho API; mọi correctness state nằm ở managed stores
  và encrypted artifact storage.
- Production Render phải dùng paid instance. Theo
  [Render free instances](https://render.com/docs/free), Free service ngủ sau
  idle, cold start dài, filesystem ephemeral và thiếu nhiều production
  capability; chỉ phù hợp smoke/demo, không phù hợp latency SLO.

### 13.2 Gate chọn region trước khi tạo Render service

Render region không đổi trực tiếp sau khi tạo service. Qdrant hiện lộ endpoint
`eu-west-2`, còn vị trí thực của Supabase/Neo4j phải xác minh. Trước khi chọn:

1. Đo DNS/TCP/TLS + query p50/p95 từ Render candidate region tới cả ba store và
   OpenAI bằng cùng một harness, cold/warm, ít nhất 100 lượt.
2. Tính request critical path và egress/compliance, không chỉ ping.
3. Nếu có thể co-locate toàn bộ managed data ở APAC, ưu tiên Singapore cho user
   Việt Nam; nếu chưa migrate data, chọn region có tổng p95 tốt nhất và ghi debt.
4. Lưu decision record. Muốn đổi region sau này phải blue/green service + DNS,
   không sửa production tại chỗ.

### 13.3 Render backend contract

Tạo `render.yaml` ở root và validate theo
[Render Blueprint specification](https://render.com/docs/blueprint-spec). Blueprint
tối thiểu phải version hóa:

- Docker web service, paid plan, selected region, `healthCheckPath: /health`;
- graceful shutdown đủ cho SSE, deploy trigger policy và exact branch;
- non-secret config inline; mọi secret `sync: false` hoặc secret group;
- không tự chạy database migration ở mỗi replica startup.

Sửa Docker runtime trước deploy, theo contract của
[Render Docker runtime](https://render.com/docs/docker):

- allowlist copy chỉ `src/` và runtime metadata; tách `requirements-runtime`
  khỏi dev/pipeline; pin bằng lock/hash;
- multi-stage venv tại `/opt/venv`, chạy non-root, read-only source khi có thể;
- bind `0.0.0.0:${PORT}` do Render inject, không hard-code 8000;
- `.dockerignore` chặn `.env*`, `.git`, `.venv`, `data/`, `database/backups/`,
  audit/eval results, `outsource/`, `var/`, `web/node_modules`, `web/.next`;
- build sạch phải báo context/image size, SBOM và dependency/secret scan;
- không dùng secret làm Docker `ARG`, không ghi env/config vào build log.

Health semantics theo [Render health checks](https://render.com/docs/health-checks):

- `/health`: liveness rất nhẹ, trả 2xx nhanh, không gọi provider;
- `/ready`: dependency/release parity để smoke/monitor và quyết định canary,
  không dùng check nặng gây restart loop;
- startup prewarm pool/clients có deadline; nếu release contract sai thì fail
  readiness, không trả answer mixed/stale.

Migration/release không đặt trong API startup. Phương án ưu tiên là protected CI
workflow với `MIGRATION_DATABASE_URL` riêng, approval environment, dry-run,
backup/restore gate, migrate, parity và sau đó mới deploy. Render `preDeploy`
chỉ là phương án thay thế sau khi hiểu rõ nó chạy instance riêng và filesystem
thay đổi không được mang sang service; xem
[Render deploy lifecycle](https://render.com/docs/deploys).

Render secret ownership:

| Secret/config | Nơi lưu | Ghi chú |
|---|---|---|
| Runtime `DATABASE_URL` | Render secret | least-privilege, pool budget rõ |
| Migration DB URL | GitHub protected environment | không cấp cho API |
| Qdrant/Neo4j/OpenAI/Langfuse keys | Render secret | rotate + per-environment |
| Firebase Admin credential mới | [Render secret file](https://render.com/docs/docker-secrets)/ADC | rotate key đã lộ; không env example/log |
| Public Firebase client config | Vercel env | public by design, vẫn tách environment |

### 13.4 Vercel frontend contract

Tạo một Vercel project với Root Directory `web` theo
[Vercel build configuration](https://vercel.com/docs/builds/configure-a-build).
Next.js auto-detect; CI và Vercel phải dùng lockfile + `npm ci`, rồi ESLint,
`tsc --noEmit` và `next build`.

| Environment | `NEXT_PUBLIC_API_URL` | Firebase project/domain |
|---|---|---|
| Development | local API | development Firebase |
| Preview | stable staging Render URL | staging Firebase, không prod data |
| Production | production Render URL | production Firebase |

Sáu `NEXT_PUBLIC_FIREBASE_*` value và API URL phải cấu hình riêng cho
Development/Preview/Production. Theo
[Vercel environment variables](https://vercel.com/docs/environment-variables),
thay đổi env chỉ có hiệu lực với deployment mới; `NEXT_PUBLIC_*` được bundle
vào client nên được xem là public và phải rebuild theo
[Next.js environment/security guidance](https://vercel.com/academy/nextjs-foundations/env-and-security).
Firebase Admin credential tuyệt đối không xuất hiện trên Vercel.

Frontend/API changes trước deploy:

- `sendChatMessage` lấy token mới (`getIdToken`) trước request, gửi bearer và
  refresh/retry đúng một lần khi 401; backend bảo vệ `/chat` và `/analyze`;
- stable staging domain được authorize trong Firebase. Không whitelist wildcard
  cho mọi preview URL; preview lạ phải qua deployment protection;
- direct browser → Render SSE để tránh một proxy timeout/buffering không cần
  thiết; propagate AbortController khi user stop/navigation;
- thêm CSP/security headers, `frame-ancestors`, nosniff, referrer/permissions
  policy và kiểm tra Firebase/OpenAI endpoints thật sự cần trong CSP;
- production source maps/error logs không chứa token, prompt hay PII.

Nếu cần version hóa route/header behavior, dùng
[`vercel.json`](https://vercel.com/docs/project-configuration/vercel-json) hoặc
Next config, nhưng không thêm rewrite/proxy nếu direct API hoạt động tốt. Bật
[Vercel Deployment Protection](https://vercel.com/docs/deployment-protection)
cho preview; xác minh giới hạn protection của plan đang dùng trước khi cho
preview truy cập staging backend.

### 13.5 CORS, auth và network contract

- Backend allowlist đúng production + stable staging origins; không `*` khi có
  credential. Nếu buộc hỗ trợ preview, dùng regex hẹp cho đúng team/project và
  vẫn yêu cầu Firebase auth.
- `Authorization`, `Content-Type`, `X-Request-ID` là allow headers; expose
  `X-Request-ID`; preflight có max-age hợp lý.
- UID từ verified Firebase token là identity duy nhất; không tin UID trong body.
- Rate limit theo UID + IP fallback; global admission trước body read, body và
  history/token ceiling, per-provider concurrency và monthly cost kill switch.
- Admin endpoint bắt buộc token + server-side role; không tin client route guard.
- Firebase Admin init/readiness fail closed ở production; không fallback mơ hồ
  sang missing ADC. Log stable error code, không raw token/exception.

### 13.6 CI/CD và rollout không downtime

Required checks theo thứ tự:

1. Secret/license/dependency scan; Ruff, backend test, package test + coverage.
2. Frontend lint, TypeScript, production build và dependency audit.
3. Migration checksum + disposable forward migration + generated schema diff.
4. Release contract/parity tests cho PostgreSQL/Qdrant/Neo4j.
5. Docker clean build, context allowlist, non-root/liveness/readiness smoke, SBOM.
6. Release-locked deterministic eval; optional RAGAS/human gate theo lịch.
7. Deploy Render staging → `/health`/`/ready` → authenticated exact/hybrid/SSE
   smoke → load/cancellation test.
8. Deploy Vercel preview trỏ staging; login, chat, citation, CORS và browser smoke.
9. Approval production → backend canary/blue-green → Vercel production → synthetic
   smoke → theo dõi error/TTFT/cost.
10. Nếu gate sai: rollback service version và active release pointer/projections;
    không sửa tay dữ liệu active.

Cancel superseded CI runs nhưng luôn upload audit/eval/smoke artifacts. Retention
cho deploy artifact phải đủ rollback; pin action SHA và builder/base image digest.

### 13.7 File deliverables bắt buộc trước deployment

| File/nhóm file | Kết quả cần có |
|---|---|
| `.dockerignore` | deny large/generated/secret paths; clean context report |
| `Dockerfile` | slim pinned runtime, `/opt/venv`, non-root, `$PORT`, graceful |
| `requirements/runtime.lock` | exact runtime dependency set/hashes |
| `render.yaml` | paid web, region decision, health, env ownership |
| `web/next.config.mjs` hoặc `web/vercel.json` | security headers/config cần thiết |
| CI workflows | backend/data/frontend/container/migration/eval/deploy gates |
| `database/postgres/migrations/*` | authority duy nhất + fresh bootstrap users schema |
| `database/postgres/schema.sql` | fresh-bootstrap/check snapshot |
| `ops/runbooks/*` | deploy, rollback, restore, release cutover, secret rotation |
| dashboards/alerts | availability, readiness, stage p95, TTFT, cost, quality drift |

Go-live gate cuối:

- Secret scan/image inspection hiện không thấy server secret trong tracked
  files, Docker context hoặc image; credential rotation/revocation vẫn chưa
  được thực hiện và là go-live blocker.
- Exact CORS/auth/rate limit/body limit/admin role tests pass.
- Render paid, correct region, `$PORT`, health/readiness và graceful SSE pass.
- Vercel prod/preview env tách biệt; không có Admin secret/public misconfiguration.
- Fresh forward migration và PostgreSQL disposable restore pass; full
  PostgreSQL+Qdrant+Neo4j active/previous rollback drill chưa pass.
- Current human-adjudicated quality + p95/load/cost gates chưa được chứng minh.
- Runbook/observability ownership, dashboard, budget alert và on-call chưa
  được nghiệm thu trên môi trường production thật.

## 14. Roadmap triển khai ưu tiên

### Phase 0 — khóa baseline, credentials và rollback assets (1–2 ngày)

- [ ] Rotate các credentials từng được chia sẻ ngoài secret manager; cập nhật
      local secret store nhưng không ghi value vào Git/PLAN/log.
- [ ] Revoke Firebase Admin key đã bị lộ, tạo credential mới và kiểm tra key cũ
      không còn hiệu lực; `.env.example` chỉ giữ placeholder rỗng cho Admin.
- [x] Chụp live inventory machine-readable của Supabase/Qdrant/Neo4j.
- [x] Đã tạo compressed PostgreSQL backup + Neo4j export/rebuild manifest,
  backup mới chứa projection registry; PostgreSQL disposable restore và
  Qdrant/Neo4j projection restore vào Docker target đã pass parity tại
  `eval/results/local-parity-projection.json`.
- [x] Ghi `qdrant_point_count=14393`, model, dimensions, collection fingerprint
      và artifact hash vào final Supabase release manifest.
- [x] Backfill/verify 14.393 Supabase embedding input hashes từ active artifact;
      Qdrant payload phải match 100%.
- [x] Readiness so sánh độc lập Supabase expected count ↔ Qdrant actual count ↔
      Neo4j node/edge counts.
- [x] Mọi cache/index/projection key thêm dataset/release checksum + embedding/
      model/config fingerprint; invalidate cache cũ không đủ namespace.
- [x] Tắt LangSmith 403; dùng Langfuse duy nhất. Runtime now strips ambient
      LangSmith credentials and sets tracing off before LangGraph import; the
      live RAGAS run above completed with zero LangSmith 403 events.
- [x] Pin/lock Python dependencies và chuẩn hóa Python 3.11.
- [x] Chạy current deterministic + end-to-end smoke trong run mới, không
      overwrite historical runs: Qdrant semantic gate and local readiness load
      passed; fresh read-only 36-case RAGAS run
      (`eval/results/run-20260822-completion-audit-v3/`) hoàn tất 36/36 agent,
      30/30 source RAGAS metric records, 0 fallback, 0 metric error và 36/36
      case pass. Điểm source mean: factual correctness 0,982, response
      relevancy 0,763, faithfulness 1,000, quality 0,961; draft machine-gold
      gate PASS, còn human-adjudicated denominator 292 vẫn là gate riêng.
- [x] Chặn `chat_history` payload không giới hạn trong compatibility API.
- [x] Sửa `.dockerignore` để build context không chứa `outsource/`, `data/`,
      backups, embeddings, `var/`, Web build artifacts, eval outputs, logs hoặc
      credentials; thêm source-lock manifest nhỏ thay vì commit upstream repos.

Exit gate:

- Mixed release = 0.
- Supabase/Qdrant/Neo4j cùng dataset/fingerprint.
- Restore active release từ backup pass parity.
- Không secret nào nằm trong tracked file, Docker context hoặc image layer.
- CI backend xanh; frontend lint/build xanh.

### Phase 1 — migration authority và shadow database v2 (4–7 ngày)

- [x] Migration authority explicit bằng ordered/checksummed runner (chọn một
      authority thay vì thêm Alembic song song); migration `20260825` tạo roles
      `medipay_ops`/`medipay_corpus`/`medipay_app` và schemas `ops/corpus/app`.
- [x] Tạo `packages/corpus_pipeline`, chuyển pure module bằng compatibility shim
      và gom script thành CLI; không xóa path cũ khi import/parity gate chưa pass.
- [x] Xóa DDL execution khỏi runtime/pipeline ingest; worker gọi
      `assert_schema_migrated()` và fail closed nếu migration chưa chạy.
      `create_dataset_schema()` chỉ còn là helper bootstrap fixture/compatibility
      test, không còn được gọi bởi ingest production path.
- [x] Tạo release/projection registry, active pointer và typed corpus tables;
      `release_projections` là control-plane registry, migration/live parity đã
      xác nhận PostgreSQL 37.170, Qdrant 14.393 và Neo4j 1.901 release nodes.
- [x] Load active source vào shadow schema với internal bigint keys; local và
      live active release đều đạt 682 documents/28.285 legal units/37.170 chunks
      và zero identity/hash mismatch.
- [x] Generated FTS/search hash constraints, normalized signature column và
      minimal indexes đã có trong bootstrap/forward migrations; live migration
      rehearsal pass.
- [x] Bổ sung conversation tables/RLS và public persistence với Firebase owner
      mapping; multi-turn quality/isolation evaluation vẫn mở.
- [Partial] Shadow source-vs-v2 parity đã pass local và managed active release;
      `eval/results/shadow-rehearsal-current.json` ghi 682 documents/28.285
      legal units/37.170 chunks, mismatch 0. Loader dùng transaction-local
      timeout cho cascade lớn. Dual-read sampling 100 documents/chunks với
      mismatch 0 đã pass tại `eval/results/live-dual-read-sampling.json`;
      managed shadow cutover vẫn mở. The new `ops.active_release` pointer and guarded activation
      function are live; the local physical active/previous rollback drill now
      passes, while managed cutover remains open because only one physical
      release is retained there.

Exit gate:

- Counts/IDs/text hashes/unit spans/table cells = 100% parity.
- Exact/lexical expected results không regression.
- Qdrant/Neo4j projections cùng release và physical locator được registry quản lý.
- Database size <450 MiB trong suốt migration rehearsal.

### Phase 2 — cutover, contract và cleanup an toàn (2–4 ngày)

- [Partial] Dual-read sampling contract đã kiểm tra 100 documents và 100 chunks
      shadow-vs-public với mismatch 0 tại
      `eval/results/live-dual-read-sampling.json`; runtime vẫn giữ public source
      làm authority cho đến khi managed cutover/rollback window được ký duyệt.
- [Partial] Shadow E2E/eval/latency hiện đã pass các gate có thể xác minh:
      dual-read 100/100 không mismatch, exact 100/100, graph 100/100,
      semantic Recall@20 88,75%, readiness 100/100 và provider smoke 10/10;
      chưa switch managed active read path vì pointer vẫn chỉ có một physical
      release và chưa có rollback window được phê duyệt.
- [Partial] Giữ old schema trong rollback window và thực hiện rollback drill
      thật ở local-full; managed old-schema retention/cutover vẫn mở.
- [Partial] Đã xóa legacy pgvector callers/scripts/docs, bỏ dependency khỏi
      lock và giữ Qdrant-only production path; các vector columns/indexes còn
      giữ trong legacy schema để chờ rollback window rồi mới contract/drop.
- [ ] Export và prune exact stale Neo4j release khi rollback asset khác đã pass.
- [x] Archive/checksum rồi move-to-trash 34 derived local snapshots v2–v30;
      giữ `medical_active_v31_fully_reviewed`, raw, active/rollback embedding
      artifacts và benchmarks. Archive `database/backups/derived-snapshots-v2-v30-20260822/`
      (208 MiB gzip, checksum) và trash local còn recoverable tại
      `data/.trash/medical_active_v2-v30-20260822/`.
- [x] Chuyển toàn bộ PostgreSQL backup JSON lớn sang `.json.gz`, validate bằng
      `restore_backup.read_backup` và SHA-256 manifest
      `database/backups/json-compressed-sha256-20260822.txt`; bản gốc nằm trong
      backup trash recoverable.

Exit gate:

- Runtime không import/query pgvector path; `rg` over runtime/pipeline found no
  pgvector dependency or DDL caller.
- Chỉ một schema/migration authority và một production retrieval implementation.
- Active + rollback policy được enforce; cleanup tool refuse active target.
- Supabase sau reclaim có headroom được đo, không chỉ ước lượng.

### Phase 3 — full Docker local và managed profiles (3–6 ngày)

- [x] Split/lock runtime, pipeline và dev dependencies.
- [x] Rebuild backend image bằng `/opt/venv`, allowlist copy, non-root/read-only.
- [x] Thêm Next.js standalone multi-stage image.
- [x] Compose base + `local-full` + `managed-production` profiles.
- [x] Thêm postgres/qdrant/neo4j/redis healthchecks, one-shot migrate, corpus-worker và
      backup profiles; volumes/networks tách biệt.
- [x] Chuẩn hóa `.env.example`, `.env.local.example`, `.env.docker.example`,
      `_FILE` secrets; bảo toàn AI Log/Langfuse/Firebase config.
- [Partial] Chạy verification matrix mục 8.13 ở mức structural/service gate:
      `eval/results/deploy-contract.json` ghi compose profiles, read-only
      containers, context allowlist, image sizes và secret-marker scan; local
      health/liveness/web smoke pass, migration chạy idempotent và hai job
      migrate đồng thời đều skip đúng ledger. Qdrant/Neo4j volumes đã được
      restore và local readiness pass; volume report hiện ghi
      PostgreSQL 470,871,849 bytes (database logical size 178,696,675 bytes),
      Neo4j 541,122,727 bytes và Qdrant 12,645 bytes. Restart data/API/web,
      dependency failure → degraded → recovery và 20 concurrent readiness
      requests đã pass; bounded local chat-provider load 10/10 cũng pass
      (p95 11,62s tại `eval/results/local-chat-provider-load.json`), nhưng
      full 100+ request staging load, blue/green cutover và managed rollback
      thực tế còn mở.
      CycloneDX/SARIF evidence hiện có tại `eval/results/sbom-*.json` và
      `eval/results/cves-*-high-critical.sarif`; bản image distroless/Chainguard
      hiện có zero high/critical findings cho cả API/web/migrate/pipeline và
      structural security gate pass.
      Runtime đã nâng an toàn lên `langchain-openai==1.6.0` + `openai==2.54.0`;
      Scout full SARIF không còn vulnerable package. Production platform
      attestation vẫn mở.
      Qdrant client đã khóa `1.19.0` khớp managed và local server `v1.19.0`,
      rebuild API và corpus-worker; warning version biến mất, readiness và
      parity pass tại `eval/results/local-parity-qdrant119.json`, semantic
      Recall@20 0,8875 tại `eval/results/live-qdrant-semantic-qdrant119.json`.
      Local active collection was then reindexed with threshold 10,000:
      14,393/14,393 vectors are indexed and warm retrieval is ~0.1 ms in the
      bounded smoke at `eval/results/local-qdrant119-active-index.json`.
      Cảnh báo API key trên HTTP chỉ còn ở local Compose; managed deploy phải
      dùng HTTPS.

Exit gate:

- Clean checkout → `docker compose up` → migrate → fixture → API/web ready.
- Restart/kill/restore/cutover tests pass, không mất state hoặc mixed release.
- Runtime containers không có DDL credential, corpus artifact hoặc secret layer.

### Phase 4 — giảm perceived latency (2–4 ngày)

- [x] Cache singleton `ChatOpenAI` client.
- [x] Thêm `/api/v1/chat/stream` bằng SSE (stage events + guarded final; token-level
      provider streaming remains intentionally open).
- [x] Frontend render stage/citation progress và hỗ trợ cancel.
- [x] Thêm `conversation_id`/`turn_id` vào normal/SSE contract và stable
      server-side persistence; stream traces now carry conversation/turn
      metadata through the Langfuse span. Typed citation anchors are stored
      separately and never used as evidence.
- [x] Giới hạn answer length theo intent; prompt yêu cầu concise output.
- [x] Deterministic formatting cho metadata/policy/legal-unit enumeration:
      metadata answers use a stable field order, policy routes are deterministic,
      and legal-unit evidence is rendered by a bounded extractive formatter
      with a regression test.
- [x] Prewarm Supabase pool và Qdrant HTTP connection ở startup (best-effort).
- [x] Stage-level timeout/circuit breaker.

Exit gate:

- TTFT p95 <2,5 giây tại môi trường deploy thật.
- Full answer p50 <5 giây, p95 <8 giây cho concise benchmark.
- Exact/policy không gọi embedding/Qdrant/LLM.

Track runtime Phase 4–6 có thể bắt đầu song song với DB/Docker Phase 1–3 sau khi
Phase 0 khóa release contract, miễn là đi qua repository ports và không tạo thêm
schema/vector path thứ hai.

### Phase 5 — sửa concurrency và retrieval efficiency (2–4 ngày)

- [x] Tách DB session khỏi thời gian chờ provider ngoài.
- [x] Gộp hydration + scope expansion thành một bounded CTE/query, giữ rank
      semantic và giới hạn legal-unit sibling.
- [x] Active release cache theo immutable release event/fingerprint, không TTL 30s đơn thuần.
- [x] Retrieval/answer cache keyed bởi release + normalized query + policy/model
      fingerprint; generation answers additionally bind a SHA-256 context digest
      và chỉ cache low-risk/public intent (temporal/high-risk luôn verifier path).
- [x] Intent-gate sibling expansion.
- [x] Simple legal-unit lookup mới được early-return; temporal/relational/
      comparison intent tiếp tục decomposition + fusion.
- [x] Thay `max_context_chars` bằng token-aware whole-evidence packer.
- [x] Tune semantic focus/diversity trên held-out set: cùng 80 câu và cùng 50
      ANN candidates, diversity cap=3 giữ Recall@20 88,75% và giảm duplicate
      ratio 31,31% → 26,19%; report tại
      `eval/results/local-retrieval-ablation.json`.
- [x] Thêm bounded provider concurrency và embedding single-flight; queue,
      in-flight và provider-duration metrics giờ được phát qua protected
      `/metrics`; external dashboard/alert ownership vẫn mở.
- [x] Batch embedding/Qdrant cho explicit multi-query trong cùng một turn;
      unrelated final generations vẫn không batch. Fallback remains ordered and
      release-filtered for older Qdrant clients.
- [x] Metadata lookup giữ toàn bộ title/date/status/category trong citation
      quote; parser chấp nhận cả ký tự legacy `Ð` và chuẩn hóa sang `Đ`, nên
      các signature như `05/1999/TTLT/BLÐTBXH-BYT-BTC` đi đúng exact route;
      live smoke trả đúng document `108357` với citation metadata đã verify.

Exit gate:

- Qdrant warm p95 <600 ms và không có outlier >3 giây trong 100 queries.
- Không pool starvation ở 20 concurrent requests.
- Semantic Recall@20 không giảm dưới 88,75% baseline.

### Phase 6 — claim/citation quality (3–5 ngày)

- [Partial] Định nghĩa typed BHYT ontology tối thiểu: domain claim hiện có
      `document/status/entitlement/condition/procedure/exception` cùng subject,
      condition, entitlement và effective fields; mapping sâu document → legal
      unit → table-cell vẫn cần hoàn thiện.
- [x] Sinh structured claims trước khi render answer; guardrail luôn trả claim
      audit cùng response.
- [x] Mỗi claim giữ citation IDs và source span/hash khi citation có provenance.
- [x] Reject hoặc downgrade claim không có evidence trên high-risk routes; claim
      thường được giữ ở trạng thái `unsupported` để không tạo false entailment.
- [x] High-risk status/payment route dùng official status và verifier riêng cho
      exact metadata answers: citations carry `document_metadata`, checked-at
      timestamp/source and `provenance_verified`; the evidence verifier rejects
      an unverified status citation. Broader payment-claim auditing remains open.
- [Partial] Auditor/checklist lexical hiện đo verification, evidence IDs, source
      span/hash và citation completeness; human-adjudicated precision gate vẫn
      cần chạy trên held-out set.
- [Partial] PoC Subject–Attribute–Temporal index đã tạo `table_cell_facts`,
      gắn `document_id`, và backfill 90.438/90.438 cells trên local/live, giữ
      source selector/hash; 13.823 ô chưa có row subject, legal-unit anchor
      hiện chưa có trong payload, và exact-value/latency ablation trước khi
      promote vẫn mở. Report live tại `eval/results/live-table-cell-sat.json`.
- [x] Thêm no-answer/ambiguous response có lý do cụ thể (`no_evidence`,
      `ambiguous`, `unverified`) và regression tests.
- [x] Live RAGAS sau focused metadata formatting đạt 36/36 pass, loại bỏ
      fallback/metric error và giữ context recall 1,00 tại
      `eval/results/run-20260822-completion-audit-v3/`. Đây là machine-generated
      draft-gold gate; human adjudication/full 292 denominator và gold-policy
      sign-off vẫn là gate chất lượng production riêng.

Exit gate:

- Citation precision ≥95% trên human-adjudicated set.
- Unsupported legal claim = 0 trên high-risk test set.
- Không output internal ID, prompt text hoặc unverified graph relation.

### Phase 7 — tactical DDD migration (3–6 ngày, song song)

- [Partial] Định nghĩa domain types và ports trước, không di chuyển file hàng loạt;
      `src/domain/ports.py` và typed claim domain hiện không import SDK.
- [Partial] Bọc GraphRAG answer path bằng use case `AnswerLegalQuestion`;
      normal/stream API đã đi qua LangGraph adapter, còn release publisher và
      một số offline adapters vẫn mở.
- [Partial] Inject adapter cho LangGraph vào API application boundary; JSON và
      SSE đều đi qua `AnswerLegalQuestion`/`StreamLegalQuestion`. Các
      Supabase/Qdrant/Neo4j/OpenAI/Langfuse adapters còn cần tách hết khỏi
      service runtime để đạt full DDD gate.
- [x] Chuyển release publication thành `PublishCorpusRelease` invariant; use
      case từ chối thiếu projection/fingerprint/count parity trước publisher.
- [Partial] Xóa `example_node`, `example_tool` và unused `src/integrations/llm.py`
      sau import scan; giữ `src/graph_rag/retrieval.py` như compatibility shim
      có cảnh báo cho downstream callers.

Exit gate:

- Domain/application không import SDK Qdrant/Neo4j/SQLAlchemy/OpenAI.
- API routes chỉ gọi application use case.
- Không có retrieval implementation thứ hai trong production path.

### Phase 8 — product hardening (3–7 ngày)

- [x] Gắn Firebase verification vào `/chat` và `/analyze`; frontend lấy token
      mới, gửi bearer, xử lý refresh/401 và không tin UID từ request body.
- [x] Rate limit, body abuse protection và per-user cost quota; Redis/in-memory
      quota có kill-switch theo cửa sổ chi phí và test độc lập.
- [Partial] Chạy SLOT red-team: deterministic prompt/secret/medical/claim
      refusal, output-filter và high-risk fail-closed suite đã pass; corpus
      poisoning, retrieval manipulation, extraction và memory poisoning có thêm
      deterministic provenance fail-closed regression tại
      `eval/results/local-adversarial-suite.json` (9/9 checks pass, including
      100-item retrieval flooding and memory-hint poisoning); corpus-scale
      benchmark and live extraction against a real provider remain open.
- [x] Admin review queue dùng API/database thật; migration, admin-only RLS,
      audit events và frontend fetch/decision path đã thay mock fixtures.
- [Partial] Conversation store, ownership/RLS, bounded window, retention/delete,
      stable IDs and typed citation anchors đã có; conditional query rewrite,
      multi-turn quality set and browser/API isolation eval vẫn mở.
- [x] Bỏ `chat_history` khỏi public API sau migration; conversation history chỉ
      đọc qua owner-scoped conversation endpoints.
- [Partial] Conversation reference resolver đã re-retrieve theo typed citation
      anchor, RLS owner isolation smoke pass (`owner_visible=1`, `other_visible=0`) tại
      `eval/results/local-conversation-rls.json`; multi-turn quality set và
      cross-user API/browser eval vẫn mở.
- [N/A] OCR/invoice extraction chưa nằm trong product scope hiện tại; `/analyze`
      chỉ xử lý text đã cung cấp và giữ route an toàn yêu cầu hóa đơn/dữ liệu
      quyền lợi trước khi tính. Nếu bật product scope này, phải mở bounded
      context riêng trước khi triển khai.
- [Partial] Readiness load, backup/restore, bounded 10-request provider load và
      local physical rollback drill đã pass; managed read-only readiness
      100/100 và provider 10/10 hiện cũng pass tại
      `eval/results/live-readiness-load-100-current-v2.json` và
      `eval/results/live-chat-provider-load-current-v2.json`. Full 100+ concurrent
      chat-provider load, managed rollback, runbook ownership, dashboards và
      alerting vẫn mở.

Exit gate:

- Auth bypass, cross-user leakage và unauthorized admin access = 0.
- Body/history limits, UID/IP rate limit và cost kill switch pass load test.
- Conversation claim luôn re-retrieve/cite active release; memory-only claim = 0.

### Phase 9 — Render/Vercel staging rehearsal (2–4 ngày)

- [ ] Đo candidate regions và chốt region decision record trước khi tạo Render.
- [Partial] Docker runtime, `$PORT`, graceful SSE và `render.yaml` đã được
      lock/validate structurally (`render.yaml` trỏ `feat/data`, health `/health`,
      runtime đọc Render `$PORT`) tại
      `eval/results/platform-contract-current.json`; validate trên paid staging
      service vẫn mở.
- [ ] Tạo Vercel project root `web`, tách Development/Preview/Production env và
      bật preview protection.
- [ ] Tạo stable staging domains, Firebase authorized domain, exact CORS và CSP.
- [ ] Chạy migration/release workflow protected, rồi authenticated browser smoke,
      SSE cancel, readiness, load và dependency-failure tests.
- [ ] Chụp image/SBOM/context, stage latency/cost và restore/rollback evidence.

Exit gate:

- Staging chạy 24 giờ không mixed release/secret/auth/availability blocker.
- Frontend login → chat → streaming → citation pass trên browser thật.
- p95/cost/load gates pass từ region đã chọn; rollback drill đạt RTO/RPO đã ghi.

### Phase 10 — production canary và vận hành (1–2 ngày + theo dõi)

- [ ] Approval production secrets/migration; deploy backend canary/blue-green.
- [ ] Chạy synthetic exact, hybrid, high-risk abstention và SSE smoke trước khi
      promote Vercel production.
- [ ] Theo dõi 5xx, readiness, stage p95, TTFT, token/cost, provider saturation,
      citation verifier và quality drift; đặt budget alerts.
- [ ] Giữ previous service/image/release/projections cho rollback; không prune
      trong observation window.
- [ ] Sau 24–72 giờ ổn định mới tăng traffic/replica hoặc cleanup theo retention.

Exit gate:

- Tất cả Definition of Done mục 17 có evidence link và owner ký xác nhận.
- Không còn manual undocumented step; runbook rollback được một người khác chạy
  thành công trong rehearsal.

## 15. Chiến lược re-embed

Không re-embed toàn bộ ngay.

Chỉ tạo shadow collection khi thử một thay đổi có giả thuyết rõ:

- embedding input = title + số ký hiệu + legal path + passage;
- named vectors title/body;
- model embedding khác;
- chunking legal-unit-aware mới.

Shadow experiment phải đạt đồng thời:

- Recall@10 tăng ít nhất 5 điểm phần trăm hoặc Recall@20 tăng ≥3 điểm;
- citation precision không giảm;
- latency p95 không tăng quá 15%;
- passage/source hash contract không đổi hoặc có migration rõ;
- thắng ổn định qua ba run và human holdout.

Không embed mù 37.170 chunks. Table rows và structural passages nên tiếp tục đi
lexical/structured retrieval nếu semantic benchmark không chứng minh lợi ích.

## 16. Release gates cuối cùng

| Gate | Mục tiêu |
|---|---:|
| Supabase/Qdrant/Neo4j dataset parity | 100% |
| Qdrant point + ID + input-hash parity | 14.393/14.393, 100% |
| Mixed-release evidence | 0 |
| Exact identifier Recall@1 | 100% |
| Simple-vs-complex legal-unit routing | 100% trên targeted set |
| Verified legal-status claims | 100%; thiếu provenance phải abstain |
| Thematic Recall@20 | ≥88% baseline, target ≥92% |
| ANN overlap với exact Qdrant | ≥99% |
| Human citation precision | ≥95% |
| Claim-level citation recall | ≥95% |
| High-risk unsupported claims | 0 |
| Policy/safety deterministic pass | 100% |
| SLOT poison/injection/extraction red-team | 100% blocking cases pass |
| Agentic route max-step/deadline termination | 100% |
| TTFT p95 | <2,5 giây |
| Full concise answer p95 | <8 giây |
| Frontend lint/typecheck/build | pass; 0 error, warning có owner/deadline |
| Backend/data/eval CI | pass |
| Auth/rate limit | bắt buộc trước public launch |
| Multi-turn context resolution | ≥95% trên human set |
| Conversation-memory unsupported claims | 0 |
| Cross-user conversation leakage | 0 |
| Supabase/Qdrant/Neo4j release registry parity | 100% |
| Supabase semantic input-hash parity | 14.393/14.393 |
| Restore + active/previous rollback drill | local pass; managed open |
| PostgreSQL migration headroom | <450 MiB during rehearsal |
| Docker clean build/full-stack restart | pass |
| Docker forbidden path in context/image | 0 |
| Secret in image/context/config output | 0 |
| Render staging authenticated/SSE/load smoke | open — external platform |
| Vercel preview → staging browser E2E | open — external platform |
| Firebase Admin key cũ còn hiệu lực | 0 |
| SAT table exact-value/source-cell provenance | gate riêng phải pass nếu route được bật |

## 17. Definition of Done cho “production-ready”

Chỉ gọi hệ thống production-ready khi:

1. Current human-adjudicated eval pass các gate trên.
2. Release manifest ràng buộc độc lập cả ba store.
3. Streaming, timeout và circuit breaker hoạt động.
4. Auth, rate limit, quota và audit log hoạt động.
5. Frontend/admin không còn mock trên production path.
6. Có rollback drill PostgreSQL active pointer + physical Qdrant/Neo4j
   projections; alias không phải correctness dependency.
7. Có dashboard Langfuse cho quality, latency, cost và provider error rate.
8. Có cảnh báo rõ phạm vi corpus; không quảng bá là toàn bộ pháp luật y tế.
9. Multi-turn conversation resolve đúng reference nhưng mọi legal claim vẫn
   được retrieve/cite lại từ active release.
10. Conversation ownership, retention/delete và isolation tests đều pass.
11. Database migrations có một authority; API không có DDL credential và không
    còn pgvector production path.
12. Full Docker profile dựng được từ empty volumes, restore được backup và
    rollback active release mà không mixed evidence.
13. `database/` chỉ còn migration authority/contracts/small fixtures; pipeline
    thành package/CLI và generated state nằm ngoài source tree.
14. Render paid service và Vercel project có config/env ownership được version
    hóa, staging rehearsal/region decision và production rollback evidence.
15. Firebase key từng lộ được revoke/rotate và được kiểm chứng; container/SBOM/
    context/Frontend bundle không có server secret; chat/analyze/admin đều có
    server-side authorization.
16. Một operator khác có thể deploy, restore và rollback chỉ bằng runbook mà
    không cần thao tác ngầm hoặc sửa trực tiếp active data.

## 18. Thứ tự thực hiện được khuyến nghị

Thứ tự có ROI cao nhất:

1. Revoke/rotate credentials, khóa auth bypass, backup/restore drill và sửa
   release/hash registry.
2. Loại forbidden Docker context, pin runtime và đưa lint/test/build vào CI.
3. Tạo migration authority + package pipeline + shadow database v2; parity trước
   cutover.
4. Cutover, rollback drill rồi mới cleanup pgvector/Neo4j/local artifacts.
5. Singleton clients, tách DB session, token-aware packer và deterministic route.
6. SSE/cancellation, prewarm/region probe, cache/single-flight và concurrency.
7. Claim-level citations + verified status; chạy full human-adjudicated gates.
8. Tactical DDD migration trên ports/schema boundaries đã ổn định.
9. Admin/conversation hardening sau ownership/auth; multi-turn isolation eval.
10. Render/Vercel staging 24 giờ, browser/load/failure/rollback rehearsal.
11. Production canary + observation window rồi mới scale/cleanup.
12. Chỉ sau đó mới quyết định reranker, PPR, community/global hoặc re-embed bằng
    shadow ablation; không đưa upstream framework vào baseline theo cảm tính.

DDD không phải cách chữa latency hay accuracy. DDD ở đây chỉ nên dùng để bảo vệ
invariant release/evidence và làm code dễ thay đổi; chất lượng thực tế vẫn phải
được quyết định bằng data contract, benchmark và production traces.
