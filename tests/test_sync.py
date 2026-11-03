"""Интеграционные и функциональные тесты для эндпоинта пакетной синхронизации (/api/v1/sync)."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget, Category, Transaction, TransactionType, User


class TestSyncEndpointIntegration:
    """Тесты пакетной офлайн-синхронизации данных."""

    @pytest.mark.asyncio
    async def test_sync_unauthorized(self, client: AsyncClient):
        """Запрос синхронизации без авторизации возвращает 403 Forbidden."""
        response = await client.post("/api/v1/sync/", json={"transactions": [], "budgets": []})
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_sync_create_transactions_and_budgets(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Пакетная синхронизация создает новые транзакции и бюджеты."""
        user_id = test_user.id
        tx1_id = str(uuid.uuid4())
        tx2_id = str(uuid.uuid4())

        payload = {
            "transactions": [
                {
                    "id": tx1_id,
                    "amount": 750.0,
                    "description": "Обед в кафе",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-01T12:00:00Z",
                },
                {
                    "id": tx2_id,
                    "amount": 60000.0,
                    "description": "Аванс",
                    "category": "salary",
                    "type": "income",
                    "date": "2026-10-01T10:00:00Z",
                },
            ],
            "budgets": [
                {
                    "category": "food",
                    "limit_amount": 15000.0,
                    "month": 10,
                    "year": 2026,
                    "check_deleted": False,
                }
            ],
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }

        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()["data"]

        # Проверяем транзакции в ответе
        synced_txs = data["synced_transactions"]
        assert len(synced_txs) == 2
        assert synced_txs[0]["id"] == tx1_id
        assert Decimal(str(synced_txs[0]["amount"])) == Decimal("750.0")

        # Проверяем обогащенные бюджеты в ответе (spent должен учесть расход tx1)
        synced_budgets = data["synced_budgets"]
        assert len(synced_budgets) == 1
        assert synced_budgets[0]["category"] == "food"
        assert Decimal(str(synced_budgets[0]["limit_amount"])) == Decimal("15000.0")
        assert Decimal(str(synced_budgets[0]["spent"])) == Decimal("750.0")
        assert Decimal(str(synced_budgets[0]["remaining"])) == Decimal("14250.0")

        # Проверяем наличие записей в базе данных
        db_tx = (
            await db_session.execute(select(Transaction).filter(Transaction.id == uuid.UUID(tx1_id)))
        ).scalar_one_or_none()
        assert db_tx is not None
        assert db_tx.user_id == user_id

    @pytest.mark.asyncio
    async def test_sync_upsert_existing_transaction(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Синхронизация обновляет существующую транзакцию при совпадении ID."""
        tx_id = uuid.uuid4()
        existing_tx = Transaction(
            id=tx_id,
            user_id=test_user.id,
            amount=500.0,
            description="Старое описание",
            category=Category.transport,
            type=TransactionType.expense,
            date=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        db_session.add(existing_tx)
        await db_session.commit()

        payload = {
            "transactions": [
                {
                    "id": str(tx_id),
                    "amount": 650.0,
                    "description": "Обновленное описание",
                    "category": "transport",
                    "type": "expense",
                    "date": "2026-10-01T10:00:00Z",
                }
            ],
            "budgets": [],
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }

        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200

        # Проверяем в базе данных
        db_session.expire_all()
        updated_tx = (await db_session.execute(select(Transaction).filter(Transaction.id == tx_id))).scalar_one_or_none()
        assert updated_tx.amount == 650.0
        assert updated_tx.description == "Обновленное описание"

    @pytest.mark.asyncio
    async def test_sync_delete_budgets(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Синхронизация удаляет бюджеты по deleted_budget_ids, deleted_budgets и check_deleted."""
        user_id = test_user.id
        # Создаем 3 бюджета
        b1 = Budget(
            user_id=user_id,
            category=Category.food,
            limit_amount=10000.0,
            month=10,
            year=2026,
        )
        b2 = Budget(
            user_id=user_id,
            category=Category.transport,
            limit_amount=5000.0,
            month=10,
            year=2026,
        )
        b3 = Budget(
            user_id=user_id,
            category=Category.entertainment,
            limit_amount=7000.0,
            month=10,
            year=2026,
        )
        db_session.add_all([b1, b2, b3])
        await db_session.commit()
        await db_session.refresh(b1)

        payload = {
            "transactions": [],
            "budgets": [
                # Удаление через флаг is_deleted
                {
                    "category": "entertainment",
                    "limit_amount": 7000.0,
                    "month": 10,
                    "year": 2026,
                    "is_deleted": True,
                }
            ],
            # Удаление по ID
            "deleted_budget_ids": [str(b1.id)],
            # Удаление по категории и периоду
            "deleted_budgets": [
                {
                    "category": "transport",
                    "month": 10,
                    "year": 2026,
                }
            ],
        }

        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200

        # Проверяем, что все три бюджета удалены
        db_session.expire_all()
        remaining_budgets = (await db_session.execute(select(Budget).filter(Budget.user_id == user_id))).scalars().all()
        assert len(remaining_budgets) == 0

    @pytest.mark.asyncio
    async def test_sync_batch_multiple_items_and_enrichment(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Пакетная синхронизация 10+ транзакций и нескольких бюджетов корректно рассчитывает агрегаты."""
        tx_items = []
        for i in range(10):
            tx_items.append(
                {
                    "id": str(uuid.uuid4()),
                    "amount": 100.0,
                    "description": f"Exp {i}",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-02T10:00:00Z",
                }
            )

        budget_items = [
            {
                "category": "food",
                "limit_amount": 2000.0,
                "month": 10,
                "year": 2026,
            },
            {
                "category": "transport",
                "limit_amount": 1000.0,
                "month": 10,
                "year": 2026,
            },
        ]

        payload = {
            "transactions": tx_items,
            "budgets": budget_items,
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }

        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()["data"]

        assert len(data["synced_transactions"]) == 10
        assert len(data["synced_budgets"]) == 2

        food_budget = next(b for b in data["synced_budgets"] if b["category"] == "food")
        transport_budget = next(b for b in data["synced_budgets"] if b["category"] == "transport")

        # 10 транзакций по 100.0 = 1000.0 spent
        assert Decimal(str(food_budget["spent"])) == Decimal("1000.0")
        assert Decimal(str(food_budget["remaining"])) == Decimal("1000.0")
        assert Decimal(str(transport_budget["spent"])) == Decimal("0.00")
        assert Decimal(str(transport_budget["remaining"])) == Decimal("1000.0")

    @pytest.mark.asyncio
    async def test_sync_budgets_idempotent_upsert_and_deduplication(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Идемпотентный UPSERT бюджетов по ID и составному ключу без дубликатов и ошибок уникальности."""
        user_id = test_user.id
        client_budget_id = str(uuid.uuid4())

        # 1. Первый запрос: создание бюджета с клиентским ID
        payload1 = {
            "transactions": [],
            "budgets": [
                {
                    "id": client_budget_id,
                    "category": "food",
                    "limit_amount": 1000.0,
                    "month": 10,
                    "year": 2026,
                }
            ],
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }
        res1 = await client.post("/api/v1/sync/", json=payload1, headers=auth_headers)
        assert res1.status_code == 200
        data1 = res1.json()["data"]["synced_budgets"]
        assert len(data1) == 1
        assert data1[0]["id"] == client_budget_id
        assert Decimal(str(data1[0]["limit_amount"])) == Decimal("1000.0")

        # 2. Второй запрос: обновление того же бюджета по ID с новым limit_amount
        payload2 = {
            "transactions": [],
            "budgets": [
                {
                    "id": client_budget_id,
                    "category": "food",
                    "limit_amount": 1500.0,
                    "month": 10,
                    "year": 2026,
                }
            ],
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }
        res2 = await client.post("/api/v1/sync/", json=payload2, headers=auth_headers)
        assert res2.status_code == 200
        data2 = res2.json()["data"]["synced_budgets"]
        assert len(data2) == 1
        assert data2[0]["id"] == client_budget_id
        assert Decimal(str(data2[0]["limit_amount"])) == Decimal("1500.0")

        # 3. Третий запрос: обновление без передачи ID (по составному ключу category + month + year)
        payload3 = {
            "transactions": [],
            "budgets": [
                {
                    "id": None,
                    "category": "food",
                    "limit_amount": 2000.0,
                    "month": 10,
                    "year": 2026,
                }
            ],
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }
        res3 = await client.post("/api/v1/sync/", json=payload3, headers=auth_headers)
        assert res3.status_code == 200
        data3 = res3.json()["data"]["synced_budgets"]
        assert len(data3) == 1
        assert Decimal(str(data3[0]["limit_amount"])) == Decimal("2000.0")

        # 4. Четвертый запрос: отправка дублирующихся элементов бюджетов в одном пакете
        payload4 = {
            "transactions": [],
            "budgets": [
                {
                    "category": "food",
                    "limit_amount": 2500.0,
                    "month": 10,
                    "year": 2026,
                },
                {
                    "category": "food",
                    "limit_amount": 3000.0,
                    "month": 10,
                    "year": 2026,
                },
            ],
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }
        res4 = await client.post("/api/v1/sync/", json=payload4, headers=auth_headers)
        assert res4.status_code == 200
        data4 = res4.json()["data"]["synced_budgets"]
        # В ответе не должно быть дубликатов — ровно 1 запись
        assert len(data4) == 1
        assert Decimal(str(data4[0]["limit_amount"])) == Decimal("3000.0")

        # 5. Проверка в БД: в таблице ровно одна запись бюджета без дубликатов
        db_session.expire_all()
        query = select(Budget).where(Budget.user_id == user_id, Budget.category == Category.food)
        result = await db_session.execute(query)
        budgets_in_db = list(result.scalars().all())
        assert len(budgets_in_db) == 1
        assert budgets_in_db[0].limit_amount == Decimal("3000.0")

    @pytest.mark.asyncio
    async def test_sync_lazy_currency_rates_when_only_base_currency(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Синхронизация не обращается к курсам валют, если все транзакции в базовой валюте."""
        from unittest.mock import AsyncMock, patch

        from app.services.currency_service import CurrencyService

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock) as mock_get_rates:
            payload = {
                "transactions": [
                    {
                        "amount": 100.0,
                        "currency": "USD",
                        "description": "Покупка в USD",
                        "category": "food",
                        "type": "expense",
                        "date": "2026-10-12T12:00:00Z",
                    }
                ],
                "budgets": [],
                "deleted_budget_ids": [],
                "deleted_budgets": [],
            }
            res = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
            assert res.status_code == 200
            mock_get_rates.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_payload_max_length_exceeded_returns_422(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Превышение лимита в 500 элементов в пакете синхронизации отклоняется с кодом 422."""
        # 501 транзакция
        tx_item = {
            "amount": 100.0,
            "description": "Тест лимита",
            "category": "food",
            "type": "expense",
        }
        res_tx = await client.post(
            "/api/v1/sync/",
            json={"transactions": [tx_item] * 501, "budgets": []},
            headers=auth_headers,
        )
        assert res_tx.status_code == 422

        # 501 бюджет
        budget_item = {
            "category": "food",
            "limit_amount": 1000.0,
            "month": 10,
            "year": 2026,
        }
        res_budget = await client.post(
            "/api/v1/sync/",
            json={"transactions": [], "budgets": [budget_item] * 501},
            headers=auth_headers,
        )
        assert res_budget.status_code == 422

    @pytest.mark.asyncio
    async def test_sync_foreign_transaction_id_rejected_without_failing_batch(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Чужой ID транзакции попадает в rejected и не роняет сохранение валидных элементов батча (TASK-12)."""
        other_user_id = uuid.uuid4()
        other_user = User(
            id=other_user_id,
            email=f"other_{other_user_id.hex[:8]}@example.com",
            name="Other User",
        )
        db_session.add(other_user)

        # Чужая существующая транзакция
        other_tx_id = uuid.uuid4()
        other_tx = Transaction(
            id=other_tx_id,
            user_id=other_user_id,
            amount=Decimal("999.00"),
            description="Чужая транзакция",
            category=Category.shopping,
            type=TransactionType.expense,
            date=datetime.now(timezone.utc),
        )
        db_session.add(other_tx)
        await db_session.commit()

        # Текущий пользователь пытается отправить батч: чужой ID + свой новый ID
        my_tx_id = str(uuid.uuid4())
        payload = {
            "transactions": [
                {
                    "id": str(other_tx_id),
                    "amount": 500.0,
                    "currency": "KZT",
                    "description": "Попытка перезаписать чужую",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-10T12:00:00Z",
                },
                {
                    "id": my_tx_id,
                    "amount": 350.0,
                    "currency": "KZT",
                    "description": "Моя валидная транзакция",
                    "category": "transport",
                    "type": "expense",
                    "date": "2026-10-10T12:00:00Z",
                },
            ],
            "budgets": [],
        }

        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()["data"]

        # Валидная транзакция успешно синхронизирована
        assert len(data["synced_transactions"]) == 1
        assert data["synced_transactions"][0]["id"] == my_tx_id

        # Конфликтная транзакция помещена в rejected
        assert "rejected" in data
        assert len(data["rejected"]) == 1
        assert data["rejected"][0]["id"] == str(other_tx_id)
        assert "отклонен" in data["rejected"][0]["reason"].lower() or "конфликт" in data["rejected"][0]["reason"].lower()

        # Чужая транзакция в базе осталась неизменной
        db_session.expire_all()
        check_tx = (await db_session.execute(select(Transaction).where(Transaction.id == other_tx_id))).scalar_one_or_none()
        assert check_tx is not None
        assert check_tx.user_id == other_user_id
        assert check_tx.amount == Decimal("999.00")

    @pytest.mark.asyncio
    async def test_sync_foreign_budget_id_rejected_without_failing_batch(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Чужой ID бюджета отклоняется в rejected без падения батча (TASK-12)."""
        other_user_id = uuid.uuid4()
        other_user = User(
            id=other_user_id,
            email=f"other_b_{other_user_id.hex[:8]}@example.com",
            name="Other User 2",
        )
        db_session.add(other_user)

        other_budget_id = uuid.uuid4()
        other_budget = Budget(
            id=other_budget_id,
            user_id=other_user_id,
            category=Category.health,
            limit_amount=Decimal("5000.00"),
            month=10,
            year=2026,
        )
        db_session.add(other_budget)
        await db_session.commit()

        my_budget_id = str(uuid.uuid4())
        payload = {
            "transactions": [],
            "budgets": [
                {
                    "id": str(other_budget_id),
                    "category": "health",
                    "limit_amount": 9999.0,
                    "month": 10,
                    "year": 2026,
                },
                {
                    "id": my_budget_id,
                    "category": "food",
                    "limit_amount": 10000.0,
                    "month": 10,
                    "year": 2026,
                },
            ],
        }

        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()["data"]

        assert len(data["synced_budgets"]) == 1
        assert data["synced_budgets"][0]["id"] == my_budget_id
        assert len(data["rejected"]) == 1
        assert data["rejected"][0]["id"] == str(other_budget_id)

    @pytest.mark.asyncio
    async def test_sync_unexpected_exception_returns_500_sync_failed(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Непредвиденная системная ошибка возвращает 500 SYNC_FAILED, а не 400 (TASK-13)."""
        from unittest.mock import patch

        payload = {"transactions": [], "budgets": []}
        with patch("app.routers.sync.BudgetService.enrich_multiple", side_effect=RuntimeError("Unexpected crash")):
            response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
            assert response.status_code == 500
            data = response.json()
            assert data["code"] == "SYNC_FAILED"

    @pytest.mark.asyncio
    async def test_sync_combined_budget_deletion(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Единый запрос удаляет бюджеты из всех трех источников payload (TASK-11)."""
        user_id = test_user.id
        b1_id = uuid.uuid4()
        b2_id = uuid.uuid4()
        b3_id = uuid.uuid4()
        b1 = Budget(
            id=b1_id,
            user_id=user_id,
            category=Category.food,
            limit_amount=Decimal("1000.00"),
            month=1,
            year=2026,
        )
        b2 = Budget(
            id=b2_id,
            user_id=user_id,
            category=Category.transport,
            limit_amount=Decimal("2000.00"),
            month=2,
            year=2026,
        )
        b3 = Budget(
            id=b3_id,
            user_id=user_id,
            category=Category.entertainment,
            limit_amount=Decimal("3000.00"),
            month=3,
            year=2026,
        )
        db_session.add_all([b1, b2, b3])
        await db_session.commit()

        payload = {
            "transactions": [],
            "budgets": [
                {
                    "category": "entertainment",
                    "limit_amount": 3000.0,
                    "month": 3,
                    "year": 2026,
                    "is_deleted": True,
                }
            ],
            "deleted_budget_ids": [str(b1_id)],
            "deleted_budgets": [
                {
                    "category": "transport",
                    "limit_amount": 2000.0,
                    "month": 2,
                    "year": 2026,
                }
            ],
        }

        res = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert res.status_code == 200

        db_session.expire_all()
        remaining = (await db_session.execute(select(Budget).where(Budget.user_id == user_id))).scalars().all()
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_sync_batch_delete_transactions_idempotent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Пакетное удаление транзакций через deleted_transaction_ids идемпотентно и изолировано."""
        user_id = test_user.id
        alien_user_id = uuid.uuid4()
        alien_user = User(
            id=alien_user_id,
            email=f"alien_{alien_user_id.hex[:6]}@example.com",
            name="Alien",
            hashed_password="pwd",
        )
        db_session.add(alien_user)

        tx1_id = uuid.uuid4()
        tx2_id = uuid.uuid4()
        alien_tx_id = uuid.uuid4()

        tx1 = Transaction(
            id=tx1_id,
            user_id=user_id,
            amount=Decimal("100.00"),
            currency="KZT",
            exchange_rate=Decimal("1.0"),
            base_amount=Decimal("100.00"),
            description="Своя транзакция 1",
            category=Category.food,
            type=TransactionType.expense,
            date=datetime.now(timezone.utc),
        )
        tx2 = Transaction(
            id=tx2_id,
            user_id=user_id,
            amount=Decimal("200.00"),
            currency="KZT",
            exchange_rate=Decimal("1.0"),
            base_amount=Decimal("200.00"),
            description="Своя транзакция 2",
            category=Category.transport,
            type=TransactionType.expense,
            date=datetime.now(timezone.utc),
        )
        alien_tx = Transaction(
            id=alien_tx_id,
            user_id=alien_user_id,
            amount=Decimal("500.00"),
            currency="KZT",
            exchange_rate=Decimal("1.0"),
            base_amount=Decimal("500.00"),
            description="Чужая транзакция",
            category=Category.entertainment,
            type=TransactionType.expense,
            date=datetime.now(timezone.utc),
        )
        db_session.add_all([tx1, tx2, alien_tx])
        await db_session.commit()

        # 1. Запрос синхронизации с удалением своих ID, чужого ID и несуществующего ID
        non_existent_id = uuid.uuid4()
        payload = {
            "transactions": [],
            "budgets": [],
            "deleted_transaction_ids": [str(tx1_id), str(tx2_id), str(alien_tx_id), str(non_existent_id)],
        }
        res1 = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert res1.status_code == 200

        # Свои транзакции удалены, чужая транзакция осталась в БД
        db_session.expire_all()
        user_txs = (await db_session.execute(select(Transaction).where(Transaction.user_id == user_id))).scalars().all()
        assert len(user_txs) == 0

        alien_remains = (
            await db_session.execute(select(Transaction).where(Transaction.id == alien_tx_id))
        ).scalar_one_or_none()
        assert alien_remains is not None

        # 2. Повторный запрос идемпотентно завершается успехом (200 OK)
        res2 = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert res2.status_code == 200


class TestSyncInputLimits:
    """Границы входных данных синхронизации: запись вне границ не должна ронять весь батч с 500."""

    @staticmethod
    def _tx(description: str = "Покупка", amount: float | str = 100.0, currency: str = "USD") -> dict:
        return {
            "id": str(uuid.uuid4()),
            "amount": amount,
            "currency": currency,
            "description": description,
            "category": "food",
            "type": "expense",
            "date": "2026-10-10T12:00:00Z",
        }

    @pytest.mark.asyncio
    async def test_sync_long_description_truncated_to_column_length(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession
    ):
        """Описание длиннее 255 символов обрезается, а не роняет батч ошибкой БД."""
        item = self._tx(description="а" * 300)
        response = await client.post("/api/v1/sync/", json={"transactions": [item]}, headers=auth_headers)

        assert response.status_code == 200
        synced = response.json()["data"]["synced_transactions"]
        assert len(synced) == 1
        assert synced[0]["description"] == "а" * 255
        stored = (await db_session.execute(select(Transaction).where(Transaction.id == uuid.UUID(item["id"])))).scalar_one()
        assert len(stored.description) == 255

    @pytest.mark.asyncio
    async def test_sync_amount_above_limit_returns_422(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Сумма больше 10^12 отклоняется валидацией 422, а не ошибкой переполнения Numeric (500)."""
        item = self._tx(amount="1000000000000.01")
        response = await client.post("/api/v1/sync/", json={"transactions": [item]}, headers=auth_headers)

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_sync_budget_limit_above_limit_returns_422(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Лимит бюджета больше 10^12 отклоняется валидацией 422."""
        budget = {"category": "food", "limit_amount": "1000000000000.01", "month": 10, "year": 2026}
        response = await client.post("/api/v1/sync/", json={"budgets": [budget]}, headers=auth_headers)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_sync_base_amount_overflow_rejected_without_failing_batch(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Сумма, которая после пересчёта в базовую валюту не помещается в Numeric(18, 2), уходит в rejected."""
        test_user.base_currency = "UZS"
        await db_session.commit()

        overflow = self._tx(amount="1000000000000", currency="USD")
        valid = self._tx(amount=10.0, currency="USD")
        response = await client.post("/api/v1/sync/", json={"transactions": [overflow, valid]}, headers=auth_headers)

        assert response.status_code == 200
        data = response.json()["data"]
        assert [t["id"] for t in data["synced_transactions"]] == [valid["id"]]
        assert len(data["rejected"]) == 1
        assert data["rejected"][0]["id"] == overflow["id"]
        stored = (
            await db_session.execute(select(Transaction).where(Transaction.id == uuid.UUID(overflow["id"])))
        ).scalar_one_or_none()
        assert stored is None

    @pytest.mark.asyncio
    async def test_sync_retry_with_client_id_does_not_duplicate(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Повтор батча с тем же клиентским id (ответ первого потерян) обновляет запись, а не создаёт дубль."""
        item = self._tx(amount=42.0)
        first = await client.post("/api/v1/sync/", json={"transactions": [item]}, headers=auth_headers)
        second = await client.post("/api/v1/sync/", json={"transactions": [item]}, headers=auth_headers)

        assert first.status_code == 200
        assert second.status_code == 200
        count = (
            await db_session.execute(select(func.count(Transaction.id)).where(Transaction.user_id == test_user.id))
        ).scalar_one()
        assert count == 1


class TestTransactionInputLimits:
    """Границы сумм при создании транзакций и бюджетов через REST."""

    @pytest.mark.asyncio
    async def test_create_transaction_amount_above_limit_returns_422(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        payload = {"amount": "1000000000000.01", "description": "x", "category": "food", "type": "expense"}
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_transaction_base_amount_overflow_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        test_user: User,
    ):
        """Переполнение суммы в базовой валюте — 400 INVALID_TRANSACTION_AMOUNT, а не 500."""
        test_user.base_currency = "UZS"
        await db_session.commit()
        payload = {
            "amount": "1000000000000",
            "currency": "USD",
            "description": "x",
            "category": "food",
            "type": "expense",
        }
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)

        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_TRANSACTION_AMOUNT"

    @pytest.mark.asyncio
    async def test_create_budget_limit_above_limit_returns_422(self, client: AsyncClient, auth_headers: dict[str, str]):
        payload = {"category": "food", "limit_amount": "1000000000000.01", "month": 10, "year": 2026}
        response = await client.post("/api/v1/budgets/", json=payload, headers=auth_headers)

        assert response.status_code == 422
