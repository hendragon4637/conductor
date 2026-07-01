FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY contracts/ contracts/
COPY shared/ shared/
COPY services/executor/ services/executor/

RUN pip install --no-cache-dir -e ".[dev]" && \
    useradd -m -u 10001 svc && \
    chown -R svc /app

USER svc
ENV SERVICE=executor
EXPOSE 8091

CMD ["python", "-m", "services.executor.main"]
