"""Shared exception types."""


class WorkflowError(Exception):
    """A business-rule violation. The message is safe to show to the user."""
