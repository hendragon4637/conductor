FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY contracts/ contracts/
COPY shared/ shared/
COPY services/planner/ services/planner/

RUN pip install --no-cache-dir -e ".[dev]" && \
    useradd -m -u 10001 svc && \
    chown -R svc /app

USER svc
ENV SERVICE=planner
EXPOSE 8094

CMD ["python", "-m", "services.planner.main"]
