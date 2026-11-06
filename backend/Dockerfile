FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
# Optional mirror for slow/blocked PyPI access: docker compose build --build-arg PIP_INDEX_URL=...
ARG PIP_INDEX_URL
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+-i "$PIP_INDEX_URL"} -r requirements.txt
COPY app ./app
COPY data ./data
COPY docker-entrypoint.sh ./docker-entrypoint.sh
ENTRYPOINT ["sh", "docker-entrypoint.sh"]
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
