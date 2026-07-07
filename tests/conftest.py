from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from src.core.config import settings
from src.core.database import get_db
from src.main import app

# 1. Force the testing database URL to avoid destroying local development data!
# (Appends '_test' to the database name)
TEST_DATABASE_URL = settings.async_database_url.replace("/ims_db", "/ims_db_test")


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncGenerator[AsyncEngine]:
    """Create a single database engine for the entire test session."""

    # FIX: Use NullPool to prevent asyncpg from binding pooled connections
    # to the wrong asyncio event loop!
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, echo=False)

    # Initialize the database tables exactly once per test run
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """
    The Secret Sauce: Isolated Transaction Rollback Fixture.

    Every single test receives a session bound to an uncommitted transaction.
    When the test finishes, the transaction is rolled back. The database remains completely empty.
    """
    connection = await db_engine.connect()
    # Begin a non-ORM transaction
    transaction = await connection.begin()

    # Bind the session to the existing transaction.
    # 'join_transaction_mode="create_savepoint"' ensures that if the app code calls
    # session.commit(), it only creates a savepoint instead of actually committing the outer transaction!
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

    yield session

    # Teardown: Close session, immediately rollback everything, return connection to pool
    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """An asynchronous testing client that overrides the FastAPI database dependency."""

    # Override the live DB connection pool with our isolated rollback session
    app.dependency_overrides[get_db] = lambda: db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
