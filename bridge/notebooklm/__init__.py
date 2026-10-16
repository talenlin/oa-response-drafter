"""In-process NotebookLM bridge used by OA阅答.

The public surface intentionally stays small.  NotebookLM's private web
protocol, credential persistence and response parsing live behind
``NotebookLMGateway`` so a protocol change does not leak into the OA domain.
"""

from .client import NotebookLMClient
from .errors import AuthenticationError, NotebookLMError, ProtocolError
from .gateway import NotebookLMGateway

__all__ = [
    "AuthenticationError",
    "NotebookLMClient",
    "NotebookLMError",
    "NotebookLMGateway",
    "ProtocolError",
]
