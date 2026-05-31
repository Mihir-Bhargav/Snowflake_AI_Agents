"""The Fabric Remote MCP seam — the *only* way agents touch storage.

In Fabric this is a remote service (docs/DEPLOYMENT.md §3). For local dev/tests we use
``LocalFilesystemMCP``, which maps logical tables (e.g. ``bronze.transactions``) to
Delta-style Parquet directories on disk.
"""
from .base import MCPClient, Table
from .local import LocalFilesystemMCP
from .remote import RemoteMCP

__all__ = ["MCPClient", "Table", "LocalFilesystemMCP", "RemoteMCP"]
