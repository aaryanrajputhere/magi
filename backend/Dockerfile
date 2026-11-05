FROM python:3.12-slim
WORKDIR /app/backend
COPY pyproject.toml ./
COPY app ./app
ENV TIKTOKEN_CACHE_DIR=/app/tokenizer-cache
RUN pip install --no-cache-dir .
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
COPY seed /app/seed
RUN mkdir -p /app/data && chown 1000:1000 /app/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
