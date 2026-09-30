import pytest
from app.schemas import Plan, ColumnSchema, SourceStrategy
from app.pipeline.dedupe import generate_dedupe_key, dedupe_records, generate_quality_report


@pytest.fixture
def sample_plan():
    return Plan(
        title="Startup Plan",
        objective="Extract startup information",
        entity_type="Company",
        columns=[
            ColumnSchema(name="name", type="string", description="Company name", required=True),
            ColumnSchema(name="website", type="url", description="Website URL", required=True),
            ColumnSchema(name="hq", type="string", description="Headquarters city")
        ],
        unique_key_columns=["name"],
        search_queries=["YC companies"],
        source_strategy=SourceStrategy(max_pages=5),
        extraction_instructions="Extract company facts"
    )


def test_generate_dedupe_key():
    key1 = generate_dedupe_key({"name": "Acme Corp, Inc."}, ["name"])
    key2 = generate_dedupe_key({"name": "Acme Corp Ltd."}, ["name"])

    assert key1 == "acme"
    assert key2 == "acme"


def test_near_duplicate_merging_and_evidence_union(sample_plan):
    rec1 = {
        "values_json": {"name": "Acme Corp, Inc.", "website": "https://acme.com", "hq": "San Francisco"},
        "status": "verified",
        "row_confidence": 0.9,
        "evidence_items": [
            {"column": "name", "value": "Acme Corp, Inc.", "source_id": "src_1", "quote": "Acme Corp, Inc.", "quote_verified": True, "confidence": 0.9},
            {"column": "hq", "value": "San Francisco", "source_id": "src_1", "quote": "hq in San Francisco", "quote_verified": True, "confidence": 0.9}
        ]
    }
    rec2 = {
        "values_json": {"name": "Acme Corp Ltd", "website": "https://acme.com", "hq": "New York"},
        "status": "verified",
        "row_confidence": 0.85,
        "evidence_items": [
            {"column": "name", "value": "Acme Corp Ltd", "source_id": "src_2", "quote": "Acme Corp Ltd", "quote_verified": True, "confidence": 0.85},
            {"column": "hq", "value": "New York", "source_id": "src_2", "quote": "hq in New York", "quote_verified": True, "confidence": 0.85}
        ]
    }

    results = dedupe_records([rec1, rec2], sample_plan)

    assert len(results) == 1
    merged = results[0]
    assert merged["sources_count"] == 2
    assert len(merged["evidence_items"]) == 4
    assert merged["conflict_json"] is not None
    assert "hq" in merged["conflict_json"]


def test_quality_report_generation(sample_plan):
    sources = [
        {"url": "https://s1.com", "fetch_status": "success", "policy_status": "allowed"},
        {"url": "https://s2.com", "fetch_status": "failed", "policy_status": "allowed"},
        {"url": "https://linkedin.com", "fetch_status": "skipped", "policy_status": "blocked"}
    ]
    final_records = [
        {
            "status": "verified",
            "row_confidence": 0.95,
            "sources_count": 2,
            "values_json": {"name": "Acme", "website": "https://acme.com", "hq": None},
            "evidence_items": []
        }
    ]

    report = generate_quality_report(sources, raw_records_count=2, final_records=final_records, plan=sample_plan)

    assert report["pages_discovered"] == 3
    assert report["pages_fetched"] == 1
    assert report["pages_blocked"] == 1
    assert report["pages_failed"] == 1
    assert report["duplicates_removed"] == 1
    assert report["verified_count"] == 1
