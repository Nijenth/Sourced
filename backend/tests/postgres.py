"""Start an isolated pgvector database for backend verification."""

import secrets
import subprocess
import time
from contextlib import contextmanager
from os import environ
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from backend.app.db import database_engine

IMAGE = "pgvector/pgvector:0.8.7-pg16"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


class DockerUnavailable(RuntimeError):
    pass


def docker(*args, env=None, timeout=240):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, env=env, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed. {result.stderr.strip()}")
    return result.stdout.strip()


@contextmanager
def disposable_postgres():
    try:
        os_type = docker("info", "--format", "{{.OSType}}", timeout=10)
    except FileNotFoundError:
        raise DockerUnavailable("Docker CLI was not found on PATH.") from None
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        raise DockerUnavailable("The Docker daemon is unreachable. Start Docker to run the Postgres tests.") from None
    if os_type != "linux":
        raise DockerUnavailable("Docker must be running Linux containers for the Postgres tests.")

    suffix = uuid4().hex
    name = f"sourced-test-{suffix}"
    user = f"sourced_{suffix}"
    database = "sourced-test"
    password = secrets.token_urlsafe(32)
    docker(
        "run", "--rm", "-d", "--name", name, "--publish", "127.0.0.1::5432",
        "--tmpfs", "/var/lib/postgresql/data", "-e", f"POSTGRES_USER={user}",
        "-e", f"POSTGRES_DB={database}", "-e", "POSTGRES_PASSWORD", IMAGE,
        env={**environ, "POSTGRES_PASSWORD": password},
    )
    try:
        port = int(docker("port", name, "5432/tcp").rsplit(":", 1)[-1])
        url = URL.create("postgresql+psycopg", username=user, password=password, host="127.0.0.1", port=port,
                         database=database, query={"connect_timeout": "2"})
        engine = database_engine(url)
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    with engine.connect() as connection:
                        system_id = connection.scalar(text("SELECT system_identifier FROM pg_control_system()"))
                        assert connection.execute(text("SELECT current_database(), current_user")).one() == (database, user)
                    break
                except OperationalError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Disposable Docker Postgres did not accept connections.") from None
                    time.sleep(0.25)
            container_id = docker(
                "exec", name, "psql", "-tA", "-U", user, "-d", database,
                "-c", "SELECT system_identifier FROM pg_control_system()",
            )
            assert str(system_id) == container_id, "Host connection is not reaching the disposable container."
            with engine.begin() as connection:
                assert connection.scalar(text("SELECT to_regclass('public.workspaces')")) is None
                config = Config(str(ALEMBIC_INI), attributes={"connection": connection})
                command.upgrade(config, "head")
                assert connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            yield engine
        finally:
            engine.dispose()
    finally:
        docker("stop", "--time", "2", name)
