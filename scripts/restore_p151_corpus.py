#!/usr/bin/env python3
"""Script một lệnh để nạp toàn bộ 683 văn bản và đồ thị vào DB của Group project.

Cách dùng:
    python scripts/restore_p151_corpus.py --target-db postgres
    python scripts/restore_p151_corpus.py --target-db neo4j
    python scripts/restore_p151_corpus.py --target-db all
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "data" / "backups" / "p151_683_full_export"

def restore_postgres(db_url: str):
    print("\n==========================================")
    print(">>> 1. NẠP TOÀN BỘ DỮ LIỆU VÀO SUPABASE (POSTGRESQL)...")
    print("==========================================")
    
    postgres_file = BACKUP_DIR / "postgres" / "postgres_full_backup.json.gz"
    if not postgres_file.is_file():
        raise FileNotFoundError(f"Không tìm thấy file backup: {postgres_file}")
    
    print("Đang đọc dữ liệu nén từ", postgres_file.name, "...")
    with gzip.open(postgres_file, "rt", encoding="utf-8") as f:
        data = json.load(f)
    
    table_order = [
        "datasets",
        "documents",
        "document_aliases",
        "legal_units",
        "document_tables",
        "table_cells",
        "table_cell_facts",
        "chunks",
        "release_projections",
        "dataset_state"
    ]
    
    # Chuẩn hóa connection URL
    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    
    with psycopg.connect(clean_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Tắt trigger tạm thời nếu cần hoặc chèn an toàn
            for table in table_order:
                rows = data.get(table, [])
                if not rows:
                    continue
                
                print(f"  -> Đang nạp bảng {table} ({len(rows)} bản ghi)...")
                
                # Lấy danh sách cột thực tế của bảng đích
                cur.execute("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_schema = 'public' AND table_name = %s
                """, (table,))
                col_info = dict(cur.fetchall())
                if not col_info:
                    print(f"     [!] Bảng {table} chưa tồn tại trên database đích, bỏ qua hoặc cần chạy migration.")
                    continue
                
                valid_cols = [c for c in rows[0].keys() if c in col_info]
                jsonb_cols = {c for c, dt in col_info.items() if dt == 'jsonb'}
                
                # Tạo câu lệnh INSERT ON CONFLICT DO NOTHING / UPDATE
                cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in valid_cols)
                placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in valid_cols)
                
                insert_stmt = sql.SQL(
                    "INSERT INTO public.{table} ({cols}) VALUES ({vals}) ON CONFLICT DO NOTHING;"
                ).format(
                    table=sql.Identifier(table),
                    cols=cols_sql,
                    vals=placeholders
                )
                
                batch_size = 1000
                total = len(rows)
                for i in range(0, total, batch_size):
                    batch = rows[i:i + batch_size]
                    values_list = []
                    for row in batch:
                        vals = []
                        for col in valid_cols:
                            v = row.get(col)
                            if col in jsonb_cols and v is not None:
                                vals.append(Jsonb(v))
                            else:
                                vals.append(v)
                        values_list.append(tuple(vals))
                    cur.executemany(insert_stmt, values_list)
                    conn.commit()
                    if total > 5000:
                        print(f"     Đã nạp {min(i + batch_size, total)}/{total}...")
                        
                print(f"     [OK] Bảng {table} hoàn tất.")
        
        # Cập nhật dataset_state cho singleton
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE public.dataset_state 
                SET active_dataset_id = 'snapshot-c439751724ab7f10',
                    last_cutover_at = now()
                WHERE singleton = true;
            """)
            conn.commit()
            print("  -> Đã cập nhật active_dataset_id = 'snapshot-c439751724ab7f10' trong dataset_state.")

    print(">>> NẠP POSTGRESQL THÀNH CÔNG!")


def restore_neo4j(uri: str, auth: tuple[str, str], database: str):
    print("\n==========================================")
    print(">>> 2. NẠP TOÀN BỘ KNOWLEDGE GRAPH VÀO NEO4J...")
    print("==========================================")
    
    neo4j_file = BACKUP_DIR / "neo4j" / "neo4j_graph.json"
    if not neo4j_file.is_file():
        raise FileNotFoundError(f"Không tìm thấy file Neo4j: {neo4j_file}")
        
    with open(neo4j_file, "r", encoding="utf-8") as f:
        graph = json.load(f)
        
    nodes = graph.get("nodes", [])
    relationships = graph.get("relationships", [])
    print(f"Đọc thành công: {len(nodes)} nodes, {len(relationships)} relationships.")
    
    with GraphDatabase.driver(uri, auth=auth) as driver:
        with driver.session(database=database) as session:
            # Tạo index trên graph_id
            try:
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.graph_id IS UNIQUE;")
            except Exception as e:
                print("Lưu ý về constraint/index:", e)
                
            print("  -> Đang ghi Document nodes...")
            batch_size = 500
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i:i+batch_size]
                session.run("""
                    UNWIND $batch AS row
                    MERGE (d:Document {graph_id: row.graph_id})
                    SET d += row.properties
                """, batch=batch)
                
            print(f"     [OK] Đã ghi {len(nodes)} nodes.")
            
            print("  -> Đang ghi Relationships...")
            grouped = {}
            for r in relationships:
                grouped.setdefault(r["type"], []).append(r)
                
            for rel_type, rel_list in grouped.items():
                print(f"     Quan hệ {rel_type}: {len(rel_list)} cạnh...")
                for i in range(0, len(rel_list), batch_size):
                    batch = rel_list[i:i+batch_size]
                    query = f"""
                        UNWIND $batch AS row
                        MATCH (s:Document {{graph_id: row.source_graph_id}})
                        MATCH (t:Document {{graph_id: row.target_graph_id}})
                        MERGE (s)-[r:`{rel_type}`]->(t)
                        SET r += row.properties
                    """
                    session.run(query, batch=batch)
            print(f"     [OK] Đã ghi toàn bộ {len(relationships)} relationships.")
            
    print(">>> NẠP NEO4J THÀNH CÔNG!")


def main():
    parser = argparse.ArgumentParser(description="Restore P-151 683-doc corpus to Group project DB")
    parser.add_argument("--target-db", choices=["postgres", "neo4j", "all"], default="all", help="Target database to restore")
    args = parser.parse_args()
    
    db_url = os.environ.get("DATABASE_URL")
    neo4j_uri = os.environ.get("NEO4J_URI")
    neo4j_user = os.environ.get("NEO4J_USERNAME")
    neo4j_pass = os.environ.get("NEO4J_PASSWORD")
    neo4j_db = os.environ.get("NEO4J_DATABASE", "neo4j")
    
    if args.target_db in ("postgres", "all"):
        if not db_url:
            print("[!] Chưa cấu hình DATABASE_URL trong .env")
        else:
            restore_postgres(db_url)
            
    if args.target_db in ("neo4j", "all"):
        if not neo4j_uri or not neo4j_pass:
            print("[!] Chưa cấu hình NEO4J_URI / NEO4J_PASSWORD trong .env")
        else:
            restore_neo4j(neo4j_uri, (neo4j_user, neo4j_pass), neo4j_db)
            
    print("\n>>> TẤT CẢ ĐÃ SẴN SÀNG! Bạn có thể kiểm tra dữ liệu và chạy thử bot.")

if __name__ == "__main__":
    main()
