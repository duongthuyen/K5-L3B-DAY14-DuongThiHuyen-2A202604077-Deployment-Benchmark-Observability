"""
Production AI Agent — Part 6 Final Project

  GET  /health  liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
  GET  /ready   readiness — 200 khi Redis OK, 503 khi Redis không kết nối được
  POST /ask     X-API-Key → rate limit (Redis) → cost guard (Redis) → LLM → ghi chi phí

Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging: mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Các field truyền qua extra={...}
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    return logging.getLogger("agent")


logger = _setup_logging()


# ─────────────────────────────────────────────────────────
# Lifespan: startup / graceful shutdown
# uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi chạy phần sau `yield` và thoát với exit code 0.
# ─────────────────────────────────────────────────────────
_in_flight = 0
_in_flight_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"event": "startup", "environment": settings.environment,
                                  "port": settings.port, "redis_ok": redis_healthy()})
    yield
    logger.info("shutdown", extra={"event": "shutdown", "in_flight": _in_flight})
    try:
        from app.redis_client import r
        r.close()
    except Exception:  # noqa: BLE001 — đang tắt, không để lỗi dọn dẹp làm exit code != 0
        pass


app = FastAPI(title="Production AI Agent", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


@app.middleware("http")
async def access_log(request: Request, call_next):
    global _in_flight
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    with _in_flight_lock:
        _in_flight += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        with _in_flight_lock:
            _in_flight -= 1
        logger.info("request", extra={
            "event": "request", "request_id": request_id, "method": request.method,
            "path": request.url.path, "status": status,
            "ms": round((time.perf_counter() - start) * 1000, 1),
        })


# ─────────────────────────────────────────────────────────
# Xử lý lỗi: mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code,
                        content={"error": exc.detail, "status": exc.status_code},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "msg": e.get("msg")}
              for e in exc.errors()]
    return JSONResponse(status_code=422,
                        content={"error": "Invalid request body", "status": 422, "details": errors})


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    logger.error("redis_unavailable", extra={"event": "redis_error", "path": request.url.path,
                                             "error": type(exc).__name__})
    return JSONResponse(status_code=503, content={"error": "Service temporarily unavailable",
                                                  "status": 503})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.error("unhandled_error", extra={"event": "unhandled_error", "path": request.url.path,
                                           "error": type(exc).__name__})
    return JSONResponse(status_code=500, content={"error": "Internal server error", "status": 500})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "/ask"]}


@app.get("/health")
def health():
    """Liveness: process còn sống → 200. Không kiểm tra Redis."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được."""
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis not reachable")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be blank")

    check_rate_limit(api_key)       # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                  # 402 khi hết DAILY/MONTHLY budget

    answer = llm_ask(question)

    input_tokens, output_tokens = estimate_tokens(question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    logger.info("llm_call", extra={"event": "llm_call", "user_id": body.user_id,
                                   "input_tokens": input_tokens, "output_tokens": output_tokens,
                                   "cost_usd": round(cost, 8)})
    return {
        "question": question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "cost_usd": round(cost, 8),
    }
