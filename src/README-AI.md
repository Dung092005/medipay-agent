# Backend source — AI contract

Preserve the ports/adapters boundary: do not put SQL, provider calls, graph
traversal or prompt assembly directly in routes. Retrieval evidence must retain
release and source-span provenance. Fail closed on missing production secrets,
invalid Firebase Admin JSON and unsafe CORS; never log secret values.
