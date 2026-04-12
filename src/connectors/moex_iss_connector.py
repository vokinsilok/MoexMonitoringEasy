import asyncio
import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


@dataclass(slots=True)
class MoexSharesPage:
    items: list[dict[str, Any]]
    next_start: int | None


class MoexISSRequestError(RuntimeError):
    pass


class MoexISSConnector:
    def __init__(
        self,
        base_url: str = "https://iss.moex.com",
        timeout: float = 5.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        allow_http_fallback_on_ssl_eof: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.allow_http_fallback_on_ssl_eof = allow_http_fallback_on_ssl_eof

    async def get_all_shares(self, limit: int = 100) -> list[dict[str, Any]]:
        all_shares: list[dict[str, Any]] = []
        start = 0
        visited_starts: set[int] = set()

        while True:
            if start in visited_starts:
                break
            visited_starts.add(start)

            page = await self.get_shares_page(start=start, limit=limit)
            if not page.items:
                break

            all_shares.extend(page.items)
            if page.next_start is None:
                break

            start = page.next_start

        return all_shares

    async def get_shares_page(self, start: int = 0, limit: int = 100) -> MoexSharesPage:
        payload = await self._request_json(
            "/iss/engines/stock/markets/shares/securities.json",
            {
                "iss.meta": "off",
                "start": start,
                "limit": limit,
            },
        )

        items = self._extract_block_rows(payload, "securities")
        next_start = self._extract_next_start(
            payload=payload,
            current_start=start,
            fetched_items=len(items),
            requested_limit=limit,
        )
        return MoexSharesPage(items=items, next_start=next_start)

    async def get_security_details(self, secid: str) -> dict[str, Any]:
        safe_secid = quote(secid, safe="")
        profile_request = self._request_json(
            f"/iss/securities/{safe_secid}.json",
            {"iss.meta": "off"},
        )
        market_request = self._request_json(
            f"/iss/engines/stock/markets/shares/securities/{safe_secid}.json",
            {"iss.meta": "off"},
        )

        profile_data, market_data = await asyncio.gather(
            profile_request,
            market_request,
            return_exceptions=True,
        )

        details: dict[str, Any] = {}
        errors: list[str] = []

        if isinstance(profile_data, Exception):
            errors.append(f"profile: {profile_data}")
        else:
            details["security_profile"] = profile_data

        if isinstance(market_data, Exception):
            errors.append(f"stock_market: {market_data}")
        else:
            details["stock_market"] = market_data

        if errors and not details:
            raise MoexISSRequestError("; ".join(errors))

        if errors:
            details["errors"] = errors

        return details

    async def _request_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        clean_path = path[1:] if path.startswith("/") else path
        request_url = f"{self.base_url}/{clean_path}"
        if params:
            request_url = f"{request_url}?{urlencode(params)}"

        def fetch(url: str) -> dict[str, Any]:
            request = Request(
                url, headers={"User-Agent": "MoexMonitorEasy/1.0", "Connection": "close"}
            )
            with urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                content = response.read().decode(charset)
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise MoexISSRequestError(f"Unexpected MOEX ISS response shape for {url}")
            return parsed

        last_exception: Exception | None = None

        total_timeout = (
            (self.timeout * self.max_retries)
            + (self.retry_backoff_seconds * max(self.max_retries - 1, 0))
            + 1.0
        )

        try:
            async with asyncio.timeout(total_timeout):
                for attempt in range(1, self.max_retries + 1):
                    try:
                        return await asyncio.to_thread(fetch, request_url)
                    except HTTPError as exc:
                        last_exception = exc
                        if 500 <= exc.code < 600 and attempt < self.max_retries:
                            await asyncio.sleep(self.retry_backoff_seconds * attempt)
                            continue
                        raise MoexISSRequestError(
                            f"MOEX ISS returned HTTP {exc.code} for {request_url}"
                        ) from exc
                    except json.JSONDecodeError as exc:
                        raise MoexISSRequestError("MOEX ISS returned non-JSON payload") from exc
                    except (ssl.SSLError, URLError, TimeoutError) as exc:
                        last_exception = exc
                        if (
                            self.allow_http_fallback_on_ssl_eof
                            and request_url.startswith("https://")
                            and self._looks_like_ssl_eof(exc)
                        ):
                            fallback_url = request_url.replace("https://", "http://", 1)
                            try:
                                return await asyncio.to_thread(fetch, fallback_url)
                            except Exception as fallback_exc:
                                last_exception = fallback_exc

                        if attempt < self.max_retries:
                            await asyncio.sleep(self.retry_backoff_seconds * attempt)
                            continue
        except TimeoutError as exc:
            raise MoexISSRequestError(
                f"MOEX ISS request exceeded {total_timeout:.1f}s budget"
            ) from exc

        raise MoexISSRequestError(f"Unable to reach MOEX ISS: {self._exc_text(last_exception)}")

    @staticmethod
    def _looks_like_ssl_eof(exc: Exception) -> bool:
        if isinstance(exc, ssl.SSLError):
            return "EOF occurred in violation of protocol" in str(
                exc
            ) or "UNEXPECTED_EOF_WHILE_READING" in str(exc)
        if isinstance(exc, URLError):
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                text = str(reason)
                return (
                    "EOF occurred in violation of protocol" in text
                    or "UNEXPECTED_EOF_WHILE_READING" in text
                )
            return "EOF occurred in violation of protocol" in str(reason)
        return False

    @staticmethod
    def _exc_text(exc: Exception | None) -> str:
        if exc is None:
            return "unknown network error"
        if isinstance(exc, URLError):
            return str(exc.reason)
        return str(exc)

    @staticmethod
    def _extract_block_rows(payload: dict[str, Any], block_name: str) -> list[dict[str, Any]]:
        block = payload.get(block_name, {})
        columns = block.get("columns", [])
        data = block.get("data", [])
        if not columns or not data:
            return []
        return [dict(zip(columns, row)) for row in data]

    def _extract_next_start(
        self,
        payload: dict[str, Any],
        current_start: int,
        fetched_items: int,
        requested_limit: int,
    ) -> int | None:
        cursor_rows = self._extract_block_rows(payload, "securities.cursor")
        if cursor_rows:
            cursor = cursor_rows[0]
            index = self._to_int(cursor.get("INDEX"), current_start)
            total = self._to_int(cursor.get("TOTAL"))
            page_size = self._to_int(cursor.get("PAGESIZE"), requested_limit)
            if total is not None and page_size is not None:
                candidate = index + page_size
                return candidate if candidate < total else None

        # For some ISS endpoints (including shares/securities) cursor is absent
        # and `start` can be ignored by the server; pagination in this case
        # would cause repeated pages and potential infinite loops.
        return None

    @staticmethod
    def _to_int(value: Any, default: int | None = None) -> int | None:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default
