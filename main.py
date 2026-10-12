"""Точка входа в приложение Finance App API.

Инициализация FastAPI приложения, подключение роутеров и управление
жизненным циклом сервиса (lifespan).
"""

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from app.database import engine, get_db
from app.exceptions import AppException, ErrorCode
from app.limiter import limiter
from app.routers import auth, budgets, currencies, insights, sync, transactions
from app.schemas.schemas import ErrorResponse
from app.tasks.token_cleanup import periodic_token_cleanup
from config_reader.config_reader import config
from logger.logger import get_logger, reset_request_id, set_request_id

logger = get_logger(__name__)

STATUS_CODE_TO_ERROR_CODE = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMIT_EXCEEDED,
    500: ErrorCode.INTERNAL_SERVER_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


background_tasks = set()


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Управление жизненным циклом приложения FastAPI.

    Выполняет инициализацию ресурсов при старте сервера, запуск регламентных фоновых задач
    и освобождение ресурсов при его завершении.

    Аргументы:
        app_instance (FastAPI): Экземпляр веб-приложения FastAPI.
    """
    logger.info("Starting Finance App API")
    cleanup_task = asyncio.create_task(periodic_token_cleanup())
    background_tasks.add(cleanup_task)
    cleanup_task.add_done_callback(background_tasks.discard)
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await engine.dispose()
    from app.services.currency_service import close_http_client

    await close_http_client()
    logger.info("Stopping Finance App API")


app = FastAPI(
    title="Finance App API",
    description="Персональный финансовый трекер с AI-инсайтами и аналитикой",
    version="0.1.0",
    lifespan=lifespan,
)

Instrumentator().instrument(app).expose(app)


app.state.limiter = limiter

# Настройка CORS для кросс-доменных запросов веб-клиентов
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=config.cors_allow_credentials,
    allow_methods=config.cors_allow_methods,
    allow_headers=config.cors_allow_headers,
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Middleware для сквозной трассировки запросов через X-Request-ID."""
    request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    token = set_request_id(request_id)
    try:
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        reset_request_id(token)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """Обработчик доменных исключений приложения с единым форматом ответа."""
    error_response = ErrorResponse(
        status="error",
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(exclude_none=True),
        headers=exc.headers,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Обработчик стандартных HTTP-исключений с единым форматом ответа."""
    if isinstance(exc, AppException):
        return await app_exception_handler(request, exc)

    code = STATUS_CODE_TO_ERROR_CODE.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else "Произошла ошибка при обработке запроса"
    if exc.status_code == 403 and message == "Not authenticated":
        message = "Отсутствует или недействителен токен авторизации"

    error_response = ErrorResponse(
        status="error",
        code=code,
        message=message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(exclude_none=True),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Обработчик ошибок валидации входных данных Pydantic."""
    errors_dict = {}
    for error in exc.errors():
        field = str(error["loc"][-1]) if error["loc"] else "unknown"
        msg = error.get("msg", "Неверное значение")
        errors_dict[field] = msg

    error_response = ErrorResponse(
        status="error",
        code=ErrorCode.VALIDATION_ERROR,
        message="Ошибка валидации входных данных",
        details={"fields": errors_dict},
    )
    return JSONResponse(
        status_code=422,
        content=error_response.model_dump(exclude_none=True),
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Обработчик ошибок превышения лимита запросов (429 RateLimitExceeded)."""
    error_response = ErrorResponse(
        status="error",
        code=ErrorCode.RATE_LIMIT_EXCEEDED,
        message="Превышен лимит запросов. Пожалуйста, повторите попытку позже.",
        details={"limit": str(exc.detail)},
    )
    return JSONResponse(
        status_code=429,
        content=error_response.model_dump(exclude_none=True),
        headers={"Retry-After": str(getattr(exc, "retry_after", 60))},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Обработчик неожиданных серверных исключений (500)."""
    logger.error(f"Неожиданная ошибка: {str(exc)}", exc_info=True)
    error_response = ErrorResponse(
        status="error",
        code=ErrorCode.INTERNAL_SERVER_ERROR,
        message="Внутренняя ошибка сервера",
    )
    return JSONResponse(
        status_code=500,
        content=error_response.model_dump(exclude_none=True),
    )


# Подключение маршрутизаторов модулей

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["transactions"])
app.include_router(budgets.router, prefix="/api/v1/budgets", tags=["budgets"])
app.include_router(insights.router, prefix="/api/v1/insights", tags=["insights"])
app.include_router(sync.router, prefix="/api/v1/sync", tags=["sync"])
app.include_router(currencies.router, prefix="/api/v1/currencies", tags=["currencies"])


@app.get("/health", summary="Проверка работоспособности сервиса")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Эндпоинт проверки здоровья и доступности API и базы данных PostgreSQL.

    Выполняет проверочный запрос SELECT 1 к базе данных.
    Если база данных недоступна, возвращает статус 503 Service Unavailable.

    Возвращает:
        dict: Статус работы сервиса, состояние БД и текущую версию API.
    """
    try:
        await db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "version": "0.1.0",
            "database": "healthy",
        }
    except Exception as exc:
        logger.error(f"Healthcheck failed: база данных недоступна: {str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                code=ErrorCode.SERVICE_UNAVAILABLE,
                message="База данных недоступна",
                details={"database": "unhealthy"},
            ).model_dump(exclude_none=True),
        )
