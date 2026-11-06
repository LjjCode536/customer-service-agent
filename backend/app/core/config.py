from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(database_url: str, database_path: str) -> str:
    """Return a SQLAlchemy URL, keeping SQLite as the zero-config default."""
    if not database_url:
        return 'sqlite:///' + Path(database_path).as_posix()
    if database_url.startswith('postgres://'):
        return 'postgresql+psycopg://' + database_url[len('postgres://'):]
    if database_url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + database_url[len('postgresql://'):]
    return database_url


class Settings(BaseSettings):
    app_name: str = 'Enterprise Customer Service & Ticket Agent'
    mock_mode: bool = True
    database_path: str = './data/customer_service.db'
    database_url: str = ''
    checkpoint_path: str = ''
    workflow_engine: str = 'langgraph'
    cors_origins: str = 'http://localhost:5173,http://localhost:3000'
    model_provider: str = 'siliconflow'
    model_api_key: str = ''
    model_base_url: str = 'https://api.siliconflow.cn/v1'
    model_chat_model: str = 'Qwen/Qwen3-8B'
    model_embedding_model: str = 'BAAI/bge-m3'
    model_embedding_dimensions: int = 1024
    model_rerank_model: str = 'BAAI/bge-reranker-v2-m3'
    model_timeout_seconds: int = 60
    model_config = SettingsConfigDict(env_file=('../.env','.env'), env_file_encoding='utf-8', extra='ignore')

    @property
    def cors_origin_list(self): return [x.strip() for x in self.cors_origins.split(',') if x.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        return normalize_database_url(self.database_url, self.database_path)

    @property
    def checkpoint_file(self) -> str:
        """Durable LangGraph checkpoint store, kept separate from business tables."""
        if self.checkpoint_path:
            return self.checkpoint_path
        url = self.sqlalchemy_url
        if url.startswith('sqlite'):
            path = url.split(':///', 1)[1] if ':///' in url else ''
            if path and path != ':memory:':
                return path + '.checkpoints'
        return './data/checkpoints.db'

    def ensure_dirs(self):
        paths = [self.checkpoint_file]
        url = self.sqlalchemy_url
        if url.startswith('sqlite') and ':///' in url:
            paths.append(url.split(':///', 1)[1])
        for path in paths:
            if path and path != ':memory:':
                Path(path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
