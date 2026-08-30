"""FastAPI 앱 진입점. 라우터는 T3+에서 app.api.v1.* 를 include_router로 붙인다."""

import boto3
import redis.asyncio as aioredis
from botocore.config import Config as BotoConfig
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import api_router
from app.config import settings
from app.db.session import engine

app = FastAPI(title="LastLetter API")
app.include_router(api_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    checks: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 - readiness probe reports whatever failed
        checks["db"] = f"error: {exc}"

    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.oci_s3_endpoint,
            aws_access_key_id=settings.oci_access_key,
            aws_secret_access_key=settings.oci_secret_key,
            region_name=settings.oci_region or None,
            config=BotoConfig(signature_version="s3v4"),
        )
        client.head_bucket(Bucket=settings.oci_bucket)
        checks["storage"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["storage"] = f"error: {exc}"

    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )
