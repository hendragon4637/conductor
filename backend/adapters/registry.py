from .base import CLIAdapter
from .opencode import OpenCodeAdapter
from .claude_code import ClaudeCodeAdapter
from .gemini import GeminiAdapter

_adapters: dict[str, type[CLIAdapter]] = {
    "opencode": OpenCodeAdapter,
    "claude_code": ClaudeCodeAdapter,
    "gemini": GeminiAdapter,
}


def get_adapter(engine: str) -> CLIAdapter:
    cls = _adapters.get(engine)
    if cls is None:
        raise ValueError(f"Unknown engine: {engine}. Known: {list(_adapters)}")
    return cls()
