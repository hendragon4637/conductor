FROM python:3.12-slim

WORKDIR /app

# git needed for watcher git-diff checks against worktrees
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY contracts/ contracts/
COPY shared/ shared/
COPY backend/ backend/
COPY services/watcher/ services/watcher/

RUN pip install --no-cache-dir -e ".[dev]" && \
    useradd -m -u 10001 svc && \
    chown -R svc /app

USER svc
ENV SERVICE=watcher
EXPOSE 8092

CMD ["python", "-m", "services.watcher.main"]
