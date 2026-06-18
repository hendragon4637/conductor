import os
from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv("/opt/aipc/conductor/.env")

_LF: Langfuse | None = None


def get_langfuse() -> Langfuse:
    global _LF
    if _LF is None:
        _LF = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            base_url=os.environ["LANGFUSE_HOST"],
        )
    return _LF
