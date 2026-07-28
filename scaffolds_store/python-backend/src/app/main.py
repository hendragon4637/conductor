from fastapi import FastAPI

app = FastAPI(title="project-name", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}
