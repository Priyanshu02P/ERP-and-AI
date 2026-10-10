from apps.core.exceptions import WorkflowError


class NoDoARuleError(WorkflowError):
    """No active DoA rule (with at least one step) covers the PR."""
