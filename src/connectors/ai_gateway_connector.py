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
