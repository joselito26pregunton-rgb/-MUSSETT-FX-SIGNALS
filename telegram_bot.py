"""Telegram transport. Never log exception text: URLs contain the bot token."""
import httpx
import logging

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class TelegramFailure(Exception):
    def __init__(self, code: str, retryable=False, ambiguous=False, retry_after=15):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after


class TelegramBot:
    def __init__(self, token: str | None, chat_id: str | None, transport=None):
        self.token = token
        self.chat_id = chat_id
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(35, connect=5), transport=transport)

    async def call(self, method: str, data=None):
        if not self.token:
            raise TelegramFailure("not_configured")
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.token}/{method}", json=data or {}
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise TelegramFailure("connection_failed", retryable=True) from None
        except httpx.HTTPError:
            raise TelegramFailure("delivery_unknown", ambiguous=True) from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramFailure("invalid_response", ambiguous=True) from None
        if response.status_code == 429:
            delay = body.get("parameters", {}).get("retry_after", 30)
            raise TelegramFailure("rate_limited", retryable=True, retry_after=max(1, min(int(delay), 3600)))
        if response.status_code >= 500:
            raise TelegramFailure("telegram_server_error", ambiguous=True)
        if response.status_code != 200 or not body.get("ok"):
            raise TelegramFailure("telegram_rejected")
        return body["result"]

    async def send(self, text):
        if not self.chat_id:
            raise TelegramFailure("chat_not_configured")
        return await self.call("sendMessage", {"chat_id": self.chat_id, "text": text})

    async def check(self):
        try:
            await self.call("getMe")
            return "connected"
        except TelegramFailure as exc:
            return exc.code

    async def close(self):
        await self.client.aclose()
