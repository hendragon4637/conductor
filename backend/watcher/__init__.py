"""Conductor Watcher — singleton supervisor, deterministic verdict, controls, gitops.

File 16: out-of-band watcher that detects done/stalled/quota-death/crashed
from ground-truth signals (File 15) and exposes resume/pause/cancel synced
to AionUi state and git worktree.
"""
from .supervisor import Watcher, get_watcher
from .verdict import verdict, VERDICT_RUNNING, VERDICT_DONE, VERDICT_STALLED, VERDICT_FAILED, VERDICT_QUOTA, VERDICT_CRASHED
from .controls import pause, resume, cancel
from .gitops import commit_chunk, regression_gate, rollback_to, merge_parallel
