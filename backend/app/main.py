from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.schema import init_schema
from app.api.router import router
@asynccontextmanager
async def lifespan(app): init_schema(); yield
app=FastAPI(title='Enterprise Customer Service & Ticket Agent',version='1.0.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origin_list,allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
@app.get('/health')
def health(): return {'status':'ok','app':settings.app_name,'mock_mode':settings.mock_mode}
app.include_router(router)
