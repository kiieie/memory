"""T2: alembic upgrade head가 실제로 전체 테이블을 만드는지 확인.

라이브 Postgres가 필요해서(DATABASE_URL) `make test`(docker compose exec api pytest)
안에서만 돈다 - 로컬 venv에서 직접 pytest 돌리면 DB 연결 실패로 스킵되지 않고 에러난다,
readyz의 db 체크와 같은 전제.
"""

import asyncio
import pathlib

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from app.db.session import engine

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "users", "consents", "recipients", "capsules", "capsule_triggers",
    "capsule_recipients", "media_assets", "deliveries", "delivery_events",
    "death_claims", "ai_jobs", "audit_logs", "alembic_version",
}


def test_upgrade_head_creates_all_tables():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")

    async def _table_names() -> list[str]:
        async with engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    tables = asyncio.run(_table_names())
    assert EXPECTED_TABLES <= set(tables)
