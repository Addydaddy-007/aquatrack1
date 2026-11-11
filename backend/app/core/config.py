"""Settings come from environment variables (see .env.example). Nothing secret is hard-coded."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str                      # Supabase Postgres connection string
    supabase_url: str = ""                 # https://<ref>.supabase.co  (used to find the JWKS keys)
    supabase_jwt_secret: str = ""          # only needed for projects that still use the legacy HS256 secret
    cors_origins: str = "http://localhost:5173"   # comma separated list of allowed frontend origins
    cors_origin_regex: str = ""            # optional, e.g. https://aquatrack-.*\.vercel\.app for preview deploys
    cron_secret: str = ""                  # protects POST /internal/run-checks (leave empty to disable)

    @property
    def cors_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json" if self.supabase_url else ""

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg2://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
