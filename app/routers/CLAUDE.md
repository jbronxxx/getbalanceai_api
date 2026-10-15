# Роутеры (app/routers/)

* Только HTTP: запрос, параметры, права, вызов сервисов. Без `db.execute(select(...))` и бизнес-логики (балансы, фильтрация, крипто, сторонние API) — всё в `app/services/`.
* Успех — `ApiResponse[T]`; списки — `ApiResponse[PaginatedResponse[T]]`. Явно задавать `response_model`, `status_code`, `summary`:
  ```python
  @router.post("/", response_model=ApiResponse[ItemResponse],
               status_code=status.HTTP_201_CREATED, summary="Краткое описание")
  ```
* OpenAPI: `APIRouter(responses={...})` со стандартными ошибками; docstring на русском; `Path`/`Query` с ограничениями (`ge`, `le`, `max_length`) и `description`.
* DI: `db: AsyncSession = Depends(get_db)`, `current_user: User = Depends(get_current_user)`.
* `@limiter.limit("X/minute")` на чувствительных к спаму/брутфорсу ручках (auth, тяжёлые отчёты, AI-инсайты).
