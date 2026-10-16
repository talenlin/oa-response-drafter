"""Errors exposed by the internal NotebookLM bridge."""


class NotebookLMError(RuntimeError):
    """Base error safe for the OA workflow to surface to the user."""


class AuthenticationError(NotebookLMError):
    """The locally persisted Google session is missing or no longer valid."""


class ProtocolError(NotebookLMError):
    """NotebookLM returned an unexpected or rejected private-protocol reply."""


class SourceUploadError(NotebookLMError):
    """A confirmed local source could not be uploaded."""
