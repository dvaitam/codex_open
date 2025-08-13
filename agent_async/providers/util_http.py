from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from typing import Dict, Optional


def _redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        q = parse_qsl(parts.query, keep_blank_values=True)
        redacted = []
        for k, v in q:
            if k.lower() in ("key", "api_key", "access_token"):
                redacted.append((k, "***"))
            else:
                redacted.append((k, v))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted), parts.fragment))
    except Exception:
        return url


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk == "authorization":
            out[k] = v.split(" ")[0] + " ***" if isinstance(v, str) else "***"
        elif lk in ("x-api-key", "api-key"):
            out[k] = "***"
        else:
            out[k] = v
    return out


def _do_http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 20, debug: bool = False) -> dict:
    base_headers = {
        "Accept": "application/json",
        "User-Agent": "agent-async/0.1 (+https://local)",
    }
    if headers:
        base_headers.update(headers)
    req = urllib.request.Request(url, headers=base_headers, method="GET")
    debug_flag = debug or bool(os.environ.get("AGENT_ASYNC_DEBUG_HTTP"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            resp_headers = dict(resp.getheaders())
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            if debug_flag:
                print(
                    f"HTTP GET {_redact_url(url)} -> {status}\nHeaders: {_redact_headers(resp_headers)}\nBody: {text[:2000]}",
                    file=sys.stderr,
                )
            return json.loads(text)
    except urllib.error.HTTPError as e:
        # Try to parse error body as JSON
        try:
            raw = e.read() if hasattr(e, "read") else b""
            text = raw.decode("utf-8", errors="replace")
            if debug_flag:
                print(f"HTTP GET {_redact_url(url)} HTTPError {e.code}: {text[:2000]}", file=sys.stderr)
            return json.loads(text)
        except Exception:
            if debug_flag:
                print(f"HTTP GET {_redact_url(url)} failed: {e}", file=sys.stderr)
            raise
    except Exception as e:
        if debug_flag:
            print(f"HTTP GET {_redact_url(url)} failed: {e}", file=sys.stderr)
        raise


async def http_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    retries: int = 0,
    backoff: float = 1.5,
    debug: bool = False,
) -> dict:
    t = timeout if timeout is not None else int(os.environ.get("AGENT_ASYNC_HTTP_TIMEOUT", "20"))
    attempt = 0
    while True:
        try:
            return await asyncio.to_thread(_do_http_get, url, headers, t, debug)
        except Exception:
            if attempt >= retries:
                raise
            await asyncio.sleep(backoff ** attempt)
            attempt += 1


def _do_http_post(url: str, body: dict, headers: Optional[Dict[str, str]] = None, timeout: int = 30, debug: bool = False) -> dict:
    base_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "agent-async/0.1 (+https://local)",
    }
    if headers:
        base_headers.update(headers)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, headers=base_headers, method="POST")
    debug_flag = debug or bool(os.environ.get("AGENT_ASYNC_DEBUG_HTTP"))
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            resp_headers = dict(resp.getheaders())
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            if debug_flag:
                print(
                    f"HTTP POST {_redact_url(url)} -> {status}\nHeaders: {_redact_headers(resp_headers)}\nBody: {text[:2000]}",
                    file=sys.stderr,
                )
            return json.loads(text)
    except urllib.error.HTTPError as e:
        # Try to parse error body as JSON for structured error data
        try:
            raw = e.read() if hasattr(e, "read") else b""
            text = raw.decode("utf-8", errors="replace")
            if debug_flag:
                print(
                    f"HTTP POST {_redact_url(url)} HTTPError {e.code}: {text[:2000]}\nPayload: {json.dumps(body)[:1000]}",
                    file=sys.stderr,
                )
            return json.loads(text)
        except Exception:
            if debug_flag:
                print(
                    f"HTTP POST {_redact_url(url)} failed: {e}\nPayload: {json.dumps(body)[:1000]}",
                    file=sys.stderr,
                )
            raise
    except Exception as e:
        if debug_flag:
            print(
                f"HTTP POST {_redact_url(url)} failed: {e}\nPayload: {json.dumps(body)[:1000]}",
                file=sys.stderr,
            )
        raise


async def http_post_json(
    url: str,
    body: dict,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    retries: int = 0,
    backoff: float = 1.5,
    debug: bool = False,
) -> dict:
    t = timeout if timeout is not None else int(os.environ.get("AGENT_ASYNC_HTTP_TIMEOUT", "30"))
    attempt = 0
    while True:
        try:
            return await asyncio.to_thread(_do_http_post, url, body, headers, t, debug)
        except Exception:
            if attempt >= retries:
                raise
            await asyncio.sleep(backoff ** attempt)
            attempt += 1
