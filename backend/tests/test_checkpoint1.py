import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import Workflow, Run
from app.schemas import Plan, ColumnSchema, SourceStrategy, ValidationRule


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_plan_pydantic_roundtrip():
    plan = Plan(
        title="Y Combinator Companies Intelligence",
        objective="Extract W24 YC startup details including name, domain, funding, and founders.",
        entity_type="Startup",
        columns=[
            ColumnSchema(name="company_name", type="string", description="Name of startup", required=True),
            ColumnSchema(name="website", type="url", description="Official company URL", required=True),
            ColumnSchema(name="batch", type="string", description="YC batch e.g. W24"),
            ColumnSchema(name="status", type="enum", description="Current operational status", enum_values=["Active", "Acquired", "Inactive"])
        ],
        unique_key_columns=["company_name"],
        search_queries=[
            "Y Combinator W24 companies directory",
            "YC Winter 2024 launch startups list"
        ],
        source_strategy=SourceStrategy(
            preferred_source_types=["official", "directory"],
            seed_urls=["https://www.ycombinator.com/companies"],
            allowed_domains=["ycombinator.com"],
            blocked_domains=["linkedin.com", "facebook.com"],
            max_pages=15
        ),
        extraction_instructions="Extract exact company details from YC company profiles.",
        validation_rules=[
            ValidationRule(column="website", rule="url_reachable")
        ],
        min_sources_for_verified=1
    )

    # Test serialization & deserialization
    json_data = plan.model_dump()
    reconstructed_plan = Plan.model_validate(json_data)

    assert reconstructed_plan.title == plan.title
    assert reconstructed_plan.entity_type == plan.entity_type
    assert len(reconstructed_plan.columns) == 4
    assert reconstructed_plan.source_strategy.max_pages == 15


def test_db_workflow_run_roundtrip(db_session):
    plan_dict = {
        "title": "Test Plan",
        "objective": "Test objective",
        "entity_type": "Company",
        "columns": [
            {"name": "name", "type": "string", "description": "Company Name", "required": True}
        ],
        "unique_key_columns": ["name"],
        "search_queries": ["test search"],
        "source_strategy": {
            "preferred_source_types": ["official"],
            "seed_urls": [],
            "allowed_domains": [],
            "blocked_domains": ["x.com"],
            "max_pages": 10
        },
        "extraction_instructions": "Extract company names",
        "validation_rules": [],
        "min_sources_for_verified": 1
    }

    # Save workflow with plan_json
    wf = Workflow(
        name="Test Workflow",
        prompt="Find AI companies",
        plan_json=plan_dict
    )
    db_session.add(wf)
    db_session.commit()
    db_session.refresh(wf)

    assert wf.id.startswith("wf_")
    assert wf.name == "Test Workflow"

    # Save run
    run = Run(
        workflow_id=wf.id,
        status="running",
        plan_snapshot=plan_dict
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    assert run.id.startswith("run_")
    assert run.status == "running"

    # Validate plan snapshot reconstituted from DB
    restored_plan = Plan.model_validate(run.plan_snapshot)
    assert restored_plan.entity_type == "Company"
