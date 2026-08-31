import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import FindingAnalysis


def _alembic_config(database_url: str) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_provider_metadata_migration_preserves_historical_deterministic_provenance(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'historical.db'}"
    command.upgrade(_alembic_config(database_url), "0008_add_finding_analysis_records")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO finding_analyses (
                analysis_id, source_finding_id, snapshot_hash, idempotency_key,
                snapshot_json, result_json, impact_paise, recoverable_impact_paise,
                claim_tag, created_at
            ) VALUES (
                :analysis_id, :source_finding_id, :snapshot_hash, :idempotency_key,
                :snapshot_json, :result_json, :impact_paise, :recoverable_impact_paise,
                :claim_tag, :created_at
            )
            """,
            {
                "analysis_id": "analysis_historical",
                "source_finding_id": "finding_historical",
                "snapshot_hash": "a" * 64,
                "idempotency_key": "historical-key",
                "snapshot_json": "{}",
                "result_json": json.dumps({"external_model_generated": False}),
                "impact_paise": 100,
                "recoverable_impact_paise": 50,
                "claim_tag": "ESTIMATED",
                "created_at": "2026-08-31 00:00:00",
            },
        )
    engine.dispose()

    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    with Session(engine) as session:
        historical = session.get(FindingAnalysis, "analysis_historical")
        assert historical is not None
        assert historical.provider == "deterministic"
        assert historical.requested_model == "deterministic-local"
        assert historical.resolved_model is None
        assert historical.provider_generation_id is None
        assert historical.prompt_version == "deterministic-finding-analysis-v1"
        assert historical.usage_json is None
        assert historical.tool_usage_json == {"requested": False, "used": False, "tools": []}
        assert historical.failure_reason is None
        assert historical.fallback_used is True

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE finding_analyses SET provider = 'openrouter' "
                "WHERE analysis_id = 'analysis_historical'"
            )
    engine.dispose()
