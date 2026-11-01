import json
import os


def test_graphdb_assertion_traits_enforced():
    """
    Wave 8: Enforce that all graph mutations (relationship signals and entity upserts)
    must carry complete confidence, evidence, lineage, and assertion_type traits.
    """
    OUTPUT_DIR = ".worktrees/feat_adversarial_review_findings_speaker_overlap/output/project_lens/10-01-2026/Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory"
    o3_path = os.path.join(OUTPUT_DIR, "03_Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory_10-01-2026_0302PM0334PMET_canonical_signal_projection.json")

    with open(o3_path) as f:
        data = json.load(f)

    mutations = data.get("graph_mutations", {})

    # 1. Check all relationship signals for traits
    for rel in mutations.get("relationship_signals", []):
        assert "assertion_type" in rel, f"Missing assertion_type in rel {rel.get('signal_id')}"
        assert "confidence" in rel, f"Missing confidence in rel {rel.get('signal_id')}"
        assert "evidence" in rel, f"Missing evidence sticky note in rel {rel.get('signal_id')}"
        assert "lineage" in rel, f"Missing lineage in rel {rel.get('signal_id')}"

    # 2. Check all entity upserts for traits
    for ent in mutations.get("entity_upserts", []):
        assert "assertion_type" in ent, f"Missing assertion_type in entity {ent.get('id')}"
        assert "confidence" in ent, f"Missing confidence in entity {ent.get('id')}"
        assert "evidence" in ent, f"Missing evidence sticky note in entity {ent.get('id')}"
        assert "lineage" in ent, f"Missing lineage in entity {ent.get('id')}"

    # 3. Verify no synthetic orphans or ungrounded scaffolds (e.g. system:conduit)
    bad_entities = {"system:conduit", "system:api-gateway", "system:persistent-database"}
    for ent in mutations.get("entity_upserts", []):
        assert ent.get("id") not in bad_entities, f"Ungrounded scaffold {ent.get('id')} found"

    # 4. Verify orphaned node checks
    # For every entity upsert, there should be at least one edge in relationship_signals involving it
    entity_ids = {e["id"] for e in mutations.get("entity_upserts", [])}
    connected_ids = set()
    for rel in mutations.get("relationship_signals", []):
        connected_ids.add(rel["from_id"])
        connected_ids.add(rel["to_id"])

    orphans = entity_ids - connected_ids
    assert len(orphans) == 0, f"Graph degradation detected! Orphaned entities found: {orphans}"
