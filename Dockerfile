# Web UI (React) build
FROM node:22-slim AS ui
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
# Chromium + driver for the mytennis.ch login of the scraper; tzdata for the daily update time
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY --from=ui /web/dist ./web/dist

RUN useradd --uid 1000 --create-home app && mkdir -p /app/data && chown app /app/data
USER app
ENV PATH=/app/.venv/bin:$PATH \
    CHROME_BIN=/usr/bin/chromium CHROMEDRIVER=/usr/bin/chromedriver \
    PORT=8000 SCRAPE_TIME=03:00 TZ=Europe/Zurich
VOLUME /app/data
CMD ["swisstennis-ranking", "--data", "/app/data", "serve"]
