# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app/backend
COPY pyproject.toml ./
# Install third-party dependencies before copying frequently changing source files.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c "import pathlib, tomllib; pathlib.Path('/tmp/requirements.txt').write_text('\n'.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))" \
    && pip install -r /tmp/requirements.txt
ENV TIKTOKEN_CACHE_DIR=/app/tokenizer-cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .
COPY seed /app/seed
RUN mkdir -p /app/data && chown 1000:1000 /app/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
