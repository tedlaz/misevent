FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra server --no-install-project
COPY . .

# Τα δεδομένα (βάσεις + κλειδιά) ζουν στο volume /data — ΠΟΤΕ στο image.
ENV MISEVENT_SAAS=1 MISEVENT_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uv", "run", "--frozen", "--no-dev", "--extra", "server", "-m", "webapp.serve"]
