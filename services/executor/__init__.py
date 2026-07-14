"""executor-svc — plan dispatch, node execution, worktree lifecycle.

Consumes ``plan.ratified`` to launch runs, ``node.steer`` to reuse an
existing AionUi conversation for fix-forward, ``node.remediate`` to
spawn a brand new node team, and ``gate.evaluated`` to finalize
worktrees or advance the node DAG.
"""
