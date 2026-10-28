"""SharePoint and OneDrive through Microsoft Search, using a delegated token."""
from __future__ import annotations

import httpx

from .base import SUPPORTED_EXTENSIONS, Document

GRAPH = "https://graph.microsoft.com/v1.0"


class GraphSource:
    def __init__(self, token: str, http: httpx.AsyncClient, scope_kql: str = ""):
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http
        self._scope = scope_kql.strip()

    async def search(self, query: str, limit: int) -> list[Document]:
        query_string = f"{query} {self._scope}".strip()
        body = {"requests": [{
            "entityTypes": ["driveItem"],
            "query": {"queryString": query_string},
            "from": 0,
            "size": min(limit * 2, 25),      # over-fetch, unsupported types get filtered
            "fields": ["id", "name", "webUrl", "eTag", "parentReference"],
        }]}
        r = await self._http.post(f"{GRAPH}/search/query", headers=self._headers, json=body, timeout=20)
        _raise_for_status(r)

        docs: list[Document] = []
        for resp in r.json().get("value", []):
            for container in resp.get("hitsContainers", []):
                for hit in container.get("hits") or []:
                    res = hit.get("resource", {})
                    name = res.get("name", "")
                    if not name.lower().endswith(SUPPORTED_EXTENSIONS):
                        continue
                    drive = res.get("parentReference", {}).get("driveId", "")
                    docs.append(Document(
                        source_id=f"{drive}/{res.get('id', '')}",
                        version=res.get("eTag", ""),
                        name=name,
                        url=res.get("webUrl", ""),
                        rank=len(docs),
                    ))
                    if len(docs) >= limit:
                        return docs
        return docs

    async def fetch(self, doc: Document) -> bytes:
        drive, item = doc.source_id.split("/", 1)
        r = await self._http.get(
            f"{GRAPH}/drives/{drive}/items/{item}/content",
            headers=self._headers, timeout=30, follow_redirects=True,
        )
        _raise_for_status(r)
        return r.content


class GraphError(Exception):
    def __init__(self, status: int, retry_after: str | None):
        super().__init__(f"Graph returned {status}")
        self.status = status
        self.retry_after = retry_after


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code >= 400:
        raise GraphError(r.status_code, r.headers.get("Retry-After"))
