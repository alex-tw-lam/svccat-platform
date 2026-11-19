FROM docker.io/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY manage.py ./
COPY catalog ./catalog
RUN uv run python manage.py collectstatic --noinput 2>/dev/null || true
RUN uv run python manage.py migrate --noinput
EXPOSE 8080
CMD ["sh", "-c", "uv run python manage.py runserver 0.0.0.0:${PORT}"]
