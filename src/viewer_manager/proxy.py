"""Reverse proxy that routes /viewer-session/{sid}/* to the viewer Pod."""

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from viewer_manager.db import session_scope
from viewer_manager.models import ViewerSession

PROXY_HEADERS_TO_PASS = [
    "accept", "accept-encoding", "accept-language", "cache-control",
    "content-type", "cookie", "referer", "user-agent",
]


async def proxy_to_viewer(session_id: str, path: str, request: Request) -> Response:
    session_factory = request.app.state.session_factory
    settings = request.app.state.settings

    with session_scope(session_factory) as db:
        vs = db.get(ViewerSession, session_id)
        if vs is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if vs.status != "READY":
            raise HTTPException(status_code=503, detail=f"Session status: {vs.status}")
        service_name = vs.service_name

    target_url = f"http://{service_name}.{settings.k8s_namespace}.svc.cluster.local/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    headers = {}
    for h in PROXY_HEADERS_TO_PASS:
        if h in request.headers:
            headers[h] = request.headers[h]

    body = await request.body()
    client = request.app.state.http_client

    resp = await client.request(
        method=request.method,
        url=target_url,
        headers=headers,
        content=body,
    )

    response_headers = dict(resp.headers)
    response_headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    response_headers["X-Frame-Options"] = "SAMEORIGIN"

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={
            k: v for k, v in response_headers.items()
            if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
        },
    )