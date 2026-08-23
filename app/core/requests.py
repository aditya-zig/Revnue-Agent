from fastapi import HTTPException, Request, status


async def read_limited_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > request.app.state.max_request_body_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    body = await request.body()
    if len(body) > request.app.state.max_request_body_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    return body
