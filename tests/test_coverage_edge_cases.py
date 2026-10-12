from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget, Category, User


@pytest.mark.asyncio
async def test_budgets_router_full_coverage(
    client: AsyncClient,
    auth_headers: dict[str, str],
    test_user: User,
    db_session: AsyncSession,
):
    # 1. Create Budget
    payload = {
        "category": "food",
        "limit_amount": 10000.0,
        "month": 10,
        "year": 2026,
    }
    response = await client.post("/api/v1/budgets/", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()["data"]
    budget_id = data["id"]
    assert data["category"] == "food"

    # 2. Get Budget by ID
    response = await client.get(f"/api/v1/budgets/{budget_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"]["id"] == budget_id

    # 3. List Budgets (without Etag)
    response = await client.get("/api/v1/budgets/?month=10&year=2026", headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    etag = response.headers.get("ETag")
    assert etag is not None

    # 4. List Budgets (with matching Etag)
    response = await client.get(
        "/api/v1/budgets/?month=10&year=2026",
        headers={**auth_headers, "If-None-Match": etag},
    )
    assert response.status_code == 304

    # 5. Delete Budget by Category and Period
    response = await client.delete("/api/v1/budgets/category/food/10/2026", headers=auth_headers)
    assert response.status_code == 200

    # 6. Delete Budget by Category and Period (Not Found)
    response = await client.delete("/api/v1/budgets/category/food/10/2026", headers=auth_headers)
    assert response.status_code == 404

    # 7. Create another and Delete by ID
    payload["category"] = "transport"
    response = await client.post("/api/v1/budgets/", json=payload, headers=auth_headers)
    budget_id = response.json()["data"]["id"]
    response = await client.delete(f"/api/v1/budgets/{budget_id}", headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_transactions_router_full_coverage(
    client: AsyncClient,
    auth_headers: dict[str, str],
    test_user: User,
):
    # 1. Create Transaction
    payload = {
        "amount": 1000.0,
        "description": "Salary",
        "category": "salary",
        "type": "income",
        "date": "2026-10-01T10:00:00Z",
    }
    response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()["data"]
    tx_id = data["id"]
    assert float(data["amount"]) == 1000.0

    # 3. List Transactions
    response = await client.get("/api/v1/transactions/?limit=10", headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()["data"]["items"]) == 1

    # 4. Delete Transaction
    response = await client.delete(f"/api/v1/transactions/{tx_id}", headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_sync_router_edge_cases(
    client: AsyncClient,
    auth_headers: dict[str, str],
    test_user: User,
):
    # Test exceptions in sync router
    payload = {
        "transactions": [],
        "budgets": [],
        "deleted_budget_ids": [],
        "deleted_budgets": [],
    }

    # Mock db.commit to raise SQLAlchemyError
    with patch("sqlalchemy.ext.asyncio.AsyncSession.commit", side_effect=SQLAlchemyError("DB Error")):
        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 500
        assert "SYNC_FAILED" in response.text

    # Mock db.commit to raise Exception
    with patch("sqlalchemy.ext.asyncio.AsyncSession.commit", side_effect=Exception("Generic Error")):
        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 500
        assert "SYNC_FAILED" in response.text


@pytest.mark.asyncio
async def test_sync_router_deleted_budgets(
    client: AsyncClient,
    auth_headers: dict[str, str],
    test_user: User,
    db_session: AsyncSession,
):
    # Add a budget to delete
    b = Budget(
        user_id=test_user.id,
        category=Category.food,
        limit_amount=1000.0,
        month=10,
        year=2026,
    )
    db_session.add(b)
    await db_session.commit()

    payload = {
        "transactions": [],
        "budgets": [
            {
                "category": "food",
                "limit_amount": 1000.0,
                "month": 10,
                "year": 2026,
                "check_deleted": True,
            }
        ],
        "deleted_budget_ids": [],
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
