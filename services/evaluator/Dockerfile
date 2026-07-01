FROM python:3.12-slim

# git + curl: git for L1 worktree operations (checkout, diff, log) and
# worktree volume management; curl for health-check probes.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY contracts/ contracts/
COPY shared/ shared/
COPY backend/ backend/
COPY services/evaluator/ services/evaluator/

RUN pip install --no-cache-dir -e ".[dev]" && \
    useradd -m -u 10001 svc && \
    chown -R svc /app

USER svc
ENV SERVICE=evaluator
ENV EVALUATOR_PORT=8093
EXPOSE 8093

CMD ["python", "-m", "services.evaluator.main"]
