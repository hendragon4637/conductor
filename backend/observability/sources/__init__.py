"""Source adapters for the observability pipeline — each returns a list[Event]."""
from .aionui_sqlite import aionui_events
from .cli_jsonl import opencode_db_events, tail_log_events, token_rate, last_activity_ts, terminal_marker, detect_quota_signal
from .worktree_fs import worktree_events, git_diff_stat, fs_changed_recently
