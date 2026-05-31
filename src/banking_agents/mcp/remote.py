"""RemoteMCP — the notebook-side client for the Fabric Remote MCP service.

In Fabric, agents run in notebooks and reach OneLake *only* through the remote MCP service
(Azure Container Apps), which holds the narrowly-scoped Fabric identity and does the actual
Delta I/O (docs/DEPLOYMENT.md §3). This client implements the SAME ``MCPClient`` interface
as ``LocalFilesystemMCP``, so agent code is byte-identical between local dev and Fabric —
only the backend swaps.

DataFrames cross the wire as Parquet bytes. This is deployment scaffolding: it requires the
MCP service to be running and reachable; it is not exercised by the local test suite.
"""
from __future__ import annotations

import io
import json
import urllib.request

import pandas as pd

from ..config import resolve_secret
from .base import MCPClient


class RemoteMCP(MCPClient):
    def __init__(self, base_url: str, token_ref: str = "env://FABRIC_MCP_TOKEN", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self._token = resolve_secret(token_ref)
        self.timeout = timeout

    def _request(self, path: str, *, data: bytes | None = None, headers: dict | None = None) -> bytes:
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method="POST" if data is not None else "GET",
            headers={"Authorization": f"Bearer {self._token}", **(headers or {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def write(self, table: str, df: pd.DataFrame, *, mode: str = "append",
              batch_id: str | None = None) -> int:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        meta = f"table={table}&mode={mode}&batch_id={batch_id or 'default'}"
        body = self._request(f"/write?{meta}", data=buf.getvalue(),
                             headers={"Content-Type": "application/octet-stream"})
        return int(json.loads(body)["rows"])

    def read(self, table: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._request(f"/read?table={table}")))

    def table_exists(self, table: str) -> bool:
        return json.loads(self._request(f"/exists?table={table}"))["exists"]

    # The MCP service also fronts the Fabric-orchestration tools; the notebook calls them here.
    def sql_query(self, query: str):
        return json.loads(self._request("/sql", data=json.dumps({"query": query}).encode(),
                                        headers={"Content-Type": "application/json"}))

    def powerbi_refresh(self, dataset: str):
        return json.loads(self._request(f"/powerbi/refresh?dataset={dataset}", data=b""))
