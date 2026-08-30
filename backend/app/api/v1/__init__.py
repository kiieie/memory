# 라우터 모음. 각 모듈 구현 후 여기서 취합해 app.main에 include_router.

from fastapi import APIRouter

from app.api.v1 import auth

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
