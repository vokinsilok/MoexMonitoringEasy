from typing import Any

import httpx


class AIGatewayRequestError(RuntimeError):
    pass


class AIGatewayConnector:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 180.0,
        max_tokens: int = 4000,
        temperature: float = 0.2,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def create_chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        reasoning: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        plugins: list[dict[str, Any]] | None = None,
        web_search_options: dict[str, Any] | None = None,
    ) -> str:
        if not self.api_key:
            raise AIGatewayRequestError("AI API key is not configured")
        if not self.model:
            raise AIGatewayRequestError("AI model is not configured")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if reasoning is not None:
            payload["reasoning"] = reasoning
        if tools:
            payload["tools"] = tools
        if plugins:
            payload["plugins"] = plugins
        if web_search_options:
            payload["web_search_options"] = web_search_options

        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise AIGatewayRequestError(
                f"AI gateway request timed out after {self.timeout:.0f} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIGatewayRequestError(f"Unable to reach AI gateway: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text.strip()
            if self._should_retry_via_proxyapi_openai(response.status_code, detail):
                return await self._create_proxyapi_openai_response(
                    messages=messages,
                    web_search_enabled=bool(plugins or web_search_options),
                )
            raise AIGatewayRequestError(
                f"AI gateway returned HTTP {response.status_code}: {detail or 'empty response'}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AIGatewayRequestError("AI gateway returned non-JSON response") from exc

        if not isinstance(data, dict):
            raise AIGatewayRequestError("AI gateway returned unexpected response shape")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIGatewayRequestError("AI gateway response does not contain choices")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise AIGatewayRequestError("AI gateway choice has unexpected shape")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise AIGatewayRequestError("AI gateway choice does not contain message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AIGatewayRequestError("AI gateway returned empty message content")
        return content.strip()

    def _should_retry_via_proxyapi_openai(self, status_code: int, detail: str) -> bool:
        if status_code != 403:
            return False
        if "api.proxyapi.ru/openrouter/" not in self.base_url:
            return False
        if not self.model.startswith("openai/"):
            return False
        return "Models for this provider are maintained by our service separately" in detail

    async def _create_proxyapi_openai_response(
        self,
        *,
        messages: list[dict[str, str]],
        web_search_enabled: bool,
    ) -> str:
        fallback_base_url = self.base_url.replace("/openrouter/v1", "/openai/v1")
        fallback_model = self.model.split("/", 1)[1]
        instructions = "\n\n".join(
            message.get("content", "")
            for message in messages
            if message.get("role") in {"system", "developer"}
        ).strip()
        input_messages = [
            {
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
            }
            for message in messages
            if message.get("role") not in {"system", "developer"}
        ]
        payload: dict[str, Any] = {
            "model": fallback_model,
            "input": input_messages,
            "max_output_tokens": self.max_tokens,
        }
        if instructions:
            payload["instructions"] = instructions
        if web_search_enabled:
            payload["tools"] = [{"type": "web_search_preview"}]

        url = f"{fallback_base_url}/responses"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise AIGatewayRequestError(
                f"AI gateway request timed out after {self.timeout:.0f} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIGatewayRequestError(f"Unable to reach AI gateway fallback: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text.strip()
            raise AIGatewayRequestError(
                f"AI gateway fallback returned HTTP {response.status_code}: {detail or 'empty response'}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AIGatewayRequestError("AI gateway fallback returned non-JSON response") from exc

        content = self._extract_responses_text(data)
        if not content:
            raise AIGatewayRequestError("AI gateway fallback returned empty response content")
        return content

    @staticmethod
    def _extract_responses_text(data: dict[str, Any]) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        chunks: list[str] = []
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
        return "\n\n".join(chunks).strip()
