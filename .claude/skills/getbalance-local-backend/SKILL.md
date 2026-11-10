---
name: getbalance-local-backend
description: Локальный бэкенд GetBalance в докере — поднять контейнеры, применить и проверить миграции, выдать токен тестового аккаунта и прогнать API через HTTP, создать и удалить временную базу Postgres для тестов. Использовать при проверке задачи бэкенда и при проверке приложения на локальном бэкенде.
---

# Локальный бэкенд — GetBalance

Каталог: `/Users/user/getbalanceai_api`. Контейнеры: `getbalanceai_api-api-1`, `-db-1`, `-redis-1`. `docker-compose.override.yml` монтирует проект в контейнер API — код берётся с хоста, после правок нужен перезапуск API. Всё только локально; прод не трогать.

```bash
API=getbalanceai_api-api-1; DB=getbalanceai_api-db-1
psql_db() { docker exec $DB sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"$1\""; }
```

## 1. Поднять контейнеры
```bash
docker info >/dev/null 2>&1 || open -a Docker
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done
docker compose up -d db redis api          # мониторинг (grafana, loki, prometheus) для проверок не нужен
for i in $(seq 1 40); do curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health | grep -q 200 && break; sleep 2; done
curl -s -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8000/health
```
* Ветка бэкенда должна совпадать с задачей (`git branch --show-current`).
* После правки кода: `docker restart $API` и снова дождаться `/health`. После правки `requirements.txt`/`Dockerfile`: `docker compose up -d --build api`.
* Контейнеры после проверки не останавливать — пользователь может ими пользоваться.

## 2. Миграции
```bash
docker exec $API alembic current
docker exec $API alembic upgrade head
# новая миграция — туда-обратно и сверка моделей со схемой:
docker exec $API alembic downgrade -1 && docker exec $API alembic upgrade head
docker exec $API alembic check             # ожидается "No new upgrade operations detected"
docker exec $DB sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d <таблица>"'
```
Если нужно проверить код без новой миграции (старый бэкенд), убрать правки `git stash push -u -m <имя>`, перезапустить API, а потом `git stash pop` и снова `upgrade head`.

## 3. Токен и запросы к API
Аккаунт для проверок — **e.makarov** (`email like 'e.makarov%'`). QA-аккаунт (qa.hamkor…) — только с отдельного разрешения пользователя в этой задаче. Токен выдаётся внутри контейнера, без пароля:
```bash
TOKEN=$(docker exec $API python -c "
import asyncio
from sqlalchemy import select
from app.database import async_session_maker
from app.models.models import User
from app.services.auth_service import AuthService
async def main():
    async with async_session_maker() as db:
        u = (await db.execute(select(User).where(User.email.like('e.makarov%')))).scalar_one()
        print(AuthService(db).create_access_token(u.id))
asyncio.run(main())
" 2>/dev/null | tail -1); echo "token ${#TOKEN} chars"
H="Authorization: Bearer $TOKEN"; B=http://127.0.0.1:8000/api/v1
curl -s "$B/auth/me" -H "$H"
curl -s -X POST "$B/sync/" -H "$H" -H 'Content-Type: application/json' -d '{...}'
curl -s -D - -o /dev/null "$B/<ресурс>/" -H "$H" -H 'If-None-Match: "<etag>"'   # 304 при совпадении
```
* Токен не печатать в ответ и не сохранять в репозитории; временные файлы — только в scratchpad.
* Если аккаунта нет в локальной базе — он появляется после входа в приложение (debug-сборка на локальном API, скилл `getbalance-device-check` в приложении) или спросить пользователя.
* Результат сверять в базе: `psql_db "select ... from <таблица> where user_id in (select id from users where email like 'e.makarov%')"`.
* Временные правки ради проверки (снижение лимитов и т.п.) — с пометкой `# TEMP` и откатом `git checkout <файл>` + `docker restart $API` сразу после проверки.

### Очистка тестовых данных
Удалять только записи, созданные в этой проверке, — по `id` или описанию, не всё у пользователя:
```bash
psql_db "delete from transactions where id in ('<id1>','<id2>')"
```
После — `git status` бэкенда без временных правок.

## 4. Тесты на временной базе Postgres
SQLite в памяти — обычный прогон; Postgres ловит длину строк, `Numeric`, ENUM, `ON CONFLICT`. Таблицы создаёт `tests/conftest.py`, миграции не нужны.
```bash
cd /Users/user/getbalanceai_api && source .venv/bin/activate
pytest tests/ -q                                       # SQLite
T=test_<задача>                                       # латиница, без дефисов
docker exec $DB sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c 'create database $T'"
PGU=$(docker exec $DB sh -c 'echo $POSTGRES_USER'); PW=$(docker exec $DB sh -c 'echo $POSTGRES_PASSWORD')
TEST_DATABASE_URL="postgresql+asyncpg://$PGU:$PW@127.0.0.1:5432/$T" pytest tests/ -q -p no:cacheprovider
docker exec $DB sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c 'drop database $T'"
```
* Базу удалять и при падении тестов. Перед удалением — убедиться, что прогон завершён (`pkill -f "pytest tests/"`, если зависший).
* Полный прогон на Postgres идёт минуты: запускать в фоне с логом в scratchpad и ждать завершения, а не фиксированный `sleep`.
* Пароль не выводить в ответ.

## 5. Итог
В ответе: `/health`, ревизия `alembic current`, результат `alembic check`, число тестов на SQLite и Postgres, какие запросы к API проверены; временная база удалена, временные правки откачены.
