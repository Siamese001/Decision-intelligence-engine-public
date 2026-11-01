"""Populate evals/hitl_qrels_queue.json with candidate queries from Wave 1 & Wave 2 where ranking competition occurred (target was not rank 1).
"""

import json
from pathlib import Path

from storage.connection import get_db_connection

sidecar_path = Path(".local/main/runtime-bge-m3-sherpa-strategic-20260729/generations/generation-187f65c8102822a78233d1629169/sidecar.sqlite")
candidates_cache = Path("evals/wave2_cached_candidates.json")

conn = get_db_connection(sidecar_path, readonly=True)

def get_record_details(record_id: str):
    row = conn.execute("SELECT artifact_json FROM retrieval_record WHERE record_id=?", (record_id,)).fetchone()
    if not row:
        return {"title": "Unknown", "snippet": ""}
    art = json.loads(row["artifact_json"])
    ext = art.get("extensions", {})
    title = ext.get("com.partner_advisory.nee.entity-title") or art.get("title", "")
    text = art.get("retrieval_text") or art.get("text", "")
    return {
        "title": title,
        "snippet": text[:250],
        "answer_use": art.get("answer_use"),
    }

cached_data = json.load(open(candidates_cache))
degraded = [c for c in cached_data if c.get("candidates") and c["candidates"][0]["record_id"] != c["target_record_id"]]

queue = []
for idx, item in enumerate(degraded, 1):
    q_text = item["query_text"]
    target_id = item["target_record_id"]
    target_info = get_record_details(target_id)

    top_candidates = []
    for rank, cand in enumerate(item["candidates"][:5], 1):
        cid = cand["record_id"]
        c_info = get_record_details(cid)
        top_candidates.append({
            "rank": rank,
            "record_id": cid,
            "title": c_info["title"],
            "snippet": c_info["snippet"],
            "relevance_label": None,  # 2 = Highly Relevant, 1 = Partially Relevant, 0 = Irrelevant
        })

    queue.append({
        "qrel_id": f"qrel-{idx:02d}",
        "query_text": q_text,
        "query_type": item.get("query_type"),
        "target_title": target_info["title"],
        "target_record_id": target_id,
        "status": "PENDING",
        "candidates": top_candidates,
    })

conn.close()

output_path = Path("evals/hitl_qrels_queue.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(queue, f, indent=2)

print(f"Populated {len(queue)} queries into {output_path}")
