from fastapi import APIRouter

router = APIRouter()


@router.get("/health", status_code=200)
def health() -> dict[str, str]:
    return {"status": "ok"}
