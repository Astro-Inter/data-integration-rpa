FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock \
    && pip check \
    && useradd --create-home --uid 10001 rpa

COPY --chown=rpa:rpa app/ ./app/

USER rpa
STOPSIGNAL SIGTERM

FROM base AS test
COPY --chown=rpa:rpa tests/ ./tests/
COPY --chown=rpa:rpa .env.example ./
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM base AS runtime
CMD ["python", "-m", "app.main"]
