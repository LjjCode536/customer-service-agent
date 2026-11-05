from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = 'Enterprise Customer Service & Ticket Agent'
    mock_mode: bool = True
    database_path: str = './data/customer_service.db'
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
    def ensure_dirs(self): Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
settings = Settings()
