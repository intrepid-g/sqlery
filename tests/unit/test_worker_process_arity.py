"""Regression test for Phase 04-01: worker_process.py:71 arity bug.

The canonical `claim_next_job_with_queue_priority` (src/sqlery/core/claiming.py)
requires three positional args: `(worker, backend, queues)`. A prior version of
`src/sqlery/django_sqlery/worker_process.py` called it with only `(worker)`,
which raised TypeError at runtime — this test prevents regression.

Strategy: AST-scan the file and assert the call passes >= 3 args.
"""

import ast
import inspect
from pathlib import Path


def test_canonical_signature_has_three_required_positional_params():
    """claim_next_job_with_queue_priority must accept (worker, backend, queues)."""
    from sqlery.core.claiming import claim_next_job_with_queue_priority

    sig = inspect.signature(claim_next_job_with_queue_priority)
    params = list(sig.parameters.values())
    # First three params must be positional (POSITIONAL_OR_KEYWORD or POSITIONAL_ONLY)
    assert len(params) >= 3, (
        f"Expected at least 3 params, got {len(params)}: {[p.name for p in params]}"
    )
    names = [p.name for p in params[:3]]
    assert names == ["worker", "backend", "queues"], (
        f"Expected first three params to be (worker, backend, queues), got {names}"
    )


def test_worker_process_call_site_passes_three_args():
    """AST-scan django_sqlery/worker_process.py and confirm the call passes 3 args."""
    import sqlery.django_sqlery.worker_process as wp_module

    src_path = Path(inspect.getfile(wp_module))
    tree = ast.parse(src_path.read_text())

    found_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "claim_next_job_with_queue_priority":
                found_calls.append(node)

    assert found_calls, "No call to claim_next_job_with_queue_priority found"
    for call in found_calls:
        total_args = len(call.args) + len(call.keywords)
        assert total_args >= 3, (
            f"Call at line {call.lineno} passes only {total_args} args "
            f"(expected >= 3: worker, backend, queues)"
        )
