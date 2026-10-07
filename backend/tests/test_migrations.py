from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from portal import models  # noqa: F401
from portal.db import Base

BACKEND = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logging"] = False
    return config


def test_migrations_produce_exactly_the_schema_the_models_describe(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")

    engine = create_engine(url)
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        indexes = inspect(connection).get_indexes("operations")
    engine.dispose()

    assert differences == []
    active = next(i for i in indexes if i["name"] == "uq_operations_one_active_per_environment")
    assert active["unique"]


def test_downgrade_removes_every_table(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert tables == {"alembic_version"}
