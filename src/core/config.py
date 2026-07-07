import os

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "IMS Enterprise SaaS"
    ENVIRONMENT: str = "development"

    # Default internal Docker network credentials
    DATABASE_URL: str = "postgresql://imsadmin:imspassword@db:5432/ims_db"

    @computed_field
    @property
    def async_database_url(self) -> str:
        """Dynamically transform URLs for asyncpg and local terminal execution."""
        url = self.DATABASE_URL

        # 1. Ensure the driver uses asyncpg
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        # 2. If running locally from the Arch terminal (outside Docker), point to mapped localhost port 5433
        if not os.path.exists("/.dockerenv") and "@db:5432" in url:
            url = url.replace("@db:5432", "@localhost:5433")

        return url

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)


settings = Settings()
