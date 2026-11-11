"""Run Sourced database migrations using the configured Postgres connection."""

from alembic import context

from backend.app.db import database_engine
from backend.app.db.models import Base


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        migrate(connection)
    else:
        engine = database_engine()
        try:
            with engine.connect() as connection:
                migrate(connection)
        finally:
            engine.dispose()
