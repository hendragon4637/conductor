"""executor-svc — plan dispatch, node execution, worktree lifecycle.

Consumes ``plan.ratified`` to launch runs, ``node.remediate`` to attempt
fix-forward, and ``gate.evaluated`` to finalize worktrees or advance
the node DAG.
"""
