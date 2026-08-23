from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
def database_url(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'reroute.db'}"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url
