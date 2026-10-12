"""Юнит и интеграционные тесты для TransactionService и эндпоинтов транзакций."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException
from app.models.models import Category, Transaction, TransactionType, User
from app.schemas.schemas import TransactionCreate
from app.services.transaction_service import TransactionService


class TestTransactionServiceUnit:
    """Юнит-тесты бизнес-логики TransactionService."""

    @pytest.mark.asyncio
    async def test_create_transaction_default_date(self, db_session: AsyncSession, test_user: User):
        """Создание транзакции без указания даты устанавливает текущее UTC время."""
        service = TransactionService(db_session)
        payload = TransactionCreate(
            amount=350.0,
            description="Кофе и круассан",
            category=Category.food,
            type=TransactionType.expense,
        )
        tx = await service.create(test_user.id, payload)

        assert tx.id is not None
        assert tx.user_id == test_user.id
        assert tx.amount == 350.0
        assert tx.description == "Кофе и круассан"
        assert tx.category == Category.food
        assert tx.type == TransactionType.expense
        assert tx.date is not None
        # Проверяем, что дата создана в районе текущего времени
        now = datetime.now(timezone.utc)
        tx_date = tx.date if tx.date.tzinfo else tx.date.replace(tzinfo=timezone.utc)
        assert abs((now - tx_date).total_seconds()) < 60

    @pytest.mark.asyncio
    async def test_create_transaction_custom_date(self, db_session: AsyncSession, test_user: User):
        """Создание транзакции с указанием конкретной даты."""
        service = TransactionService(db_session)
        custom_date = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
        payload = TransactionCreate(
            amount=50000.0,
            description="Зарплата",
            category=Category.salary,
            type=TransactionType.income,
            date=custom_date,
        )
        tx = await service.create(test_user.id, payload)

        assert tx.amount == 50000.0
        assert tx.type == TransactionType.income
        assert tx.date is not None

    @pytest.mark.asyncio
    async def test_get_all_user_isolation(self, db_session: AsyncSession, test_user: User):
        """Пользователь видит только свои транзакции."""
        service = TransactionService(db_session)
        other_user_id = uuid.uuid4()
        other_user = User(
            id=other_user_id,
            email=f"other_{other_user_id.hex[:6]}@example.com",
            name="Other User",
            hashed_password="pwd",
        )
        db_session.add(other_user)
        user_id = test_user.id
        await db_session.commit()

        # Создаем транзакцию для test_user
        await service.create(
            user_id,
            TransactionCreate(
                amount=100.0,
                description="My tx",
                category=Category.food,
                type=TransactionType.expense,
            ),
        )
        # Создаем транзакцию для other_user
        await service.create(
            other_user_id,
            TransactionCreate(
                amount=200.0,
                description="Other tx",
                category=Category.food,
                type=TransactionType.expense,
            ),
        )

        user_txs, has_more, next_cursor = await service.get_all(user_id)
        assert has_more is False
        assert len(user_txs) == 1
        assert user_txs[0].description == "My tx"

    @pytest.mark.asyncio
    async def test_get_all_filter_since(self, db_session: AsyncSession, test_user: User):
        """Фильтрация транзакций по параметру since (created_at)."""
        service = TransactionService(db_session)
        tx = await service.create(
            test_user.id,
            TransactionCreate(
                amount=150.0,
                description="Recent tx",
                category=Category.food,
                type=TransactionType.expense,
            ),
        )

        # Запрос с since в будущем не вернет ничего
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        res_empty, has_more_empty, next_cursor_empty = await service.get_all(test_user.id, since=future_time)
        assert has_more_empty is False
        assert len(res_empty) == 0

        # Запрос с since в прошлом вернет созданную транзакцию
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        res_found, has_more_found, next_cursor_found = await service.get_all(test_user.id, since=past_time)
        assert has_more_found is False
        assert len(res_found) == 1
        assert res_found[0].id == tx.id

    @pytest.mark.asyncio
    async def test_get_all_pagination_limit_and_offset(self, db_session: AsyncSession, test_user: User):
        """Пагинация limit и offset возвращает правильные срезы и общее количество."""
        service = TransactionService(db_session)
        for i in range(15):
            await service.create(
                test_user.id,
                TransactionCreate(
                    amount=10.0 + i,
                    description=f"Tx #{i}",
                    category=Category.food,
                    type=TransactionType.expense,
                ),
            )

        page1, has_more1, next_cursor1 = await service.get_all(test_user.id, limit=5)
        assert has_more1 is True
        assert len(page1) == 5
        assert next_cursor1 is not None

        page2, has_more2, next_cursor2 = await service.get_all(test_user.id, limit=5, cursor=next_cursor1)
        assert has_more2 is True
        assert len(page2) == 5
        assert {tx.id for tx in page1}.isdisjoint({tx.id for tx in page2})

        page3, has_more3, next_cursor3 = await service.get_all(test_user.id, limit=5, cursor=next_cursor2)
        assert has_more3 is False
        assert len(page3) == 5

    @pytest.mark.asyncio
    async def test_delete_transaction_success(self, db_session: AsyncSession, test_user: User):
        """Успешное удаление транзакции."""
        service = TransactionService(db_session)
        tx = await service.create(
            test_user.id,
            TransactionCreate(
                amount=150.0,
                description="To delete",
                category=Category.food,
                type=TransactionType.expense,
            ),
        )

        await service.delete(test_user.id, tx.id)
        from sqlalchemy import select

        res = await db_session.execute(select(Transaction).filter(Transaction.id == tx.id))
        assert res.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_delete_transaction_not_found(self, db_session: AsyncSession, test_user: User):
        """Попытка удаления несуществующей транзакции вызывает NotFoundException (404)."""
        service = TransactionService(db_session)
        random_id = uuid.uuid4()
        with pytest.raises(NotFoundException) as exc_info:
            await service.delete(test_user.id, random_id)
        assert exc_info.value.code == "TRANSACTION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_delete_transaction_other_user(self, db_session: AsyncSession, test_user: User):
        """Попытка удалить чужую транзакцию вызывает NotFoundException."""
        service = TransactionService(db_session)
        other_user_id = uuid.uuid4()
        other_user = User(
            id=other_user_id,
            email=f"other_{other_user_id.hex[:6]}@example.com",
            name="Other User",
            hashed_password="pwd",
        )
        db_session.add(other_user)
        user_id = test_user.id
        await db_session.commit()

        tx = await service.create(
            other_user_id,
            TransactionCreate(
                amount=150.0,
                description="Other tx",
                category=Category.food,
                type=TransactionType.expense,
            ),
        )

        with pytest.raises(NotFoundException) as exc_info:
            await service.delete(user_id, tx.id)
        assert exc_info.value.code == "TRANSACTION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_etag(self, db_session: AsyncSession, test_user: User):
        """Расчет ETag изменяется при добавлении транзакций."""
        service = TransactionService(db_session)
        etag1 = await service.get_etag(test_user.id)

        await service.create(
            test_user.id,
            TransactionCreate(
                amount=100.0,
                description="Tx 1",
                category=Category.food,
                type=TransactionType.expense,
            ),
        )
        etag2 = await service.get_etag(test_user.id)
        assert etag1 != etag2


class TestTransactionEndpointsIntegration:
    """Интеграционные тесты для эндпоинтов /api/v1/transactions/."""

    @pytest.mark.asyncio
    async def test_create_and_list_transactions(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Создание транзакции и получение списка через REST API."""
        payload = {
            "amount": 1200.50,
            "description": "Покупка продуктов",
            "category": "food",
            "type": "expense",
        }
        # Create
        create_res = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert create_res.status_code == 201
        created_data = create_res.json()["data"]
        tx_id = created_data["id"]
        assert Decimal(str(created_data["amount"])) == Decimal("1200.50")
        assert created_data["category"] == "food"

        # List
        list_res = await client.get("/api/v1/transactions/", headers=auth_headers)
        assert list_res.status_code == 200
        page_data = list_res.json()["data"]
        assert page_data["has_more"] is False
        assert page_data["limit"] == 50
        assert "next_cursor" in page_data
        tx_list = page_data["items"]
        assert len(tx_list) == 1
        assert tx_list[0]["id"] == tx_id
        assert "ETag" in list_res.headers

        # ETag caching (304 Not Modified)
        etag = list_res.headers["ETag"]
        cached_res = await client.get("/api/v1/transactions/", headers={**auth_headers, "If-None-Match": etag})
        assert cached_res.status_code == 304

        # Delete
        del_res = await client.delete(f"/api/v1/transactions/{tx_id}", headers=auth_headers)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "success"

        # Check list is now empty
        empty_res = await client.get("/api/v1/transactions/", headers=auth_headers)
        assert empty_res.status_code == 200
        assert empty_res.json()["data"]["has_more"] is False
        assert len(empty_res.json()["data"]["items"]) == 0

    @pytest.mark.asyncio
    async def test_pagination_params_and_validation(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Проверка работы параметров пагинации limit, offset и валидации границ."""
        # Создаем несколько транзакций
        for i in range(5):
            await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": 100.0 + i,
                    "description": f"Покупка {i}",
                    "category": "food",
                    "type": "expense",
                },
                headers=auth_headers,
            )

        # Получаем первую страницу
        res1 = await client.get("/api/v1/transactions/?limit=2", headers=auth_headers)
        assert res1.status_code == 200
        data1 = res1.json()["data"]
        assert data1["has_more"] is True
        assert data1["limit"] == 2
        assert len(data1["items"]) == 2
        next_cursor = data1["next_cursor"]

        # Запрашиваем следующую
        res2 = await client.get(f"/api/v1/transactions/?limit=2&cursor={next_cursor}", headers=auth_headers)
        assert res2.status_code == 200
        data2 = res2.json()["data"]
        assert data2["limit"] == 2
        assert len(data2["items"]) == 2

        # Валидация limit > 100
        res_invalid_limit = await client.get("/api/v1/transactions/?limit=101", headers=auth_headers)
        assert res_invalid_limit.status_code == 422
        assert res_invalid_limit.json()["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_etag_stable_across_page_limits_and_not_present_on_cursor_pages(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """ETag первой страницы стабилен при разных limit и отсутствует на страницах пагинации."""
        # 1. Добавляем тестовые транзакции
        for i in range(4):
            await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": 250.0 + i,
                    "description": f"Операция {i}",
                    "category": "food",
                    "type": "expense",
                },
                headers=auth_headers,
            )

        # 2. Первая страница с limit=2 и limit=4 возвращает ОДИНАКОВЫЙ глобальный ETag
        first_page_l2 = await client.get("/api/v1/transactions/?limit=2", headers=auth_headers)
        assert first_page_l2.status_code == 200
        etag_l2 = first_page_l2.headers.get("ETag")
        assert etag_l2 is not None

        first_page_l4 = await client.get("/api/v1/transactions/?limit=4", headers=auth_headers)
        assert first_page_l4.status_code == 200
        etag_l4 = first_page_l4.headers.get("ETag")
        assert etag_l4 == etag_l2

        # 3. Повторный запрос первой страницы с If-None-Match возвращает 304 с обязательным заголовком ETag
        cached_res = await client.get(
            "/api/v1/transactions/?limit=2",
            headers={**auth_headers, "If-None-Match": etag_l2},
        )
        assert cached_res.status_code == 304
        assert cached_res.headers.get("ETag") == etag_l2

        # 4. Запрос страницы пагинации с cursor и переданным If-None-Match НЕ возвращает 304 и НЕ отдает ETag
        cursor = first_page_l2.json()["data"]["next_cursor"]
        assert cursor is not None
        paged_res = await client.get(
            f"/api/v1/transactions/?limit=2&cursor={cursor}",
            headers={**auth_headers, "If-None-Match": etag_l2},
        )
        assert paged_res.status_code == 200
        assert "ETag" not in paged_res.headers
        assert len(paged_res.json()["data"]["items"]) == 2
