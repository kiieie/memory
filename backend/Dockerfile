FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

# HEALTHCHECK는 여기 안 둔다 — 이 이미지는 api(uvicorn:8000)뿐 아니라 worker/scheduler도 같이 쓰는데
# 걔넨 8000번을 안 띄워서 물려받으면 영원히 unhealthy가 된다. api용 헬스체크는 docker-compose.yml에서.

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
