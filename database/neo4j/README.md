# Neo4j knowledge graph

Neo4j là nơi duy nhất lưu knowledge graph: document nodes và các relationship
từ `data/raw/relationships.csv`. Supabase/PostgreSQL chỉ lưu document, chunk,
table và embedding vector.

## Local development

```bash
docker compose -f database/neo4j/docker-compose.yml up -d
```

Thiết lập `NEO4J_URI=bolt://localhost:7687`, `NEO4J_USERNAME=neo4j` và
`NEO4J_PASSWORD` trong `.env`. Với Aura dùng `neo4j+s://...` và password Aura.
Import release bằng script pipeline sau khi
snapshot được tạo:

```bash
python3 database/neo4j/scripts/import_relationships.py --source-dir data/raw --dataset-id <release>
```
