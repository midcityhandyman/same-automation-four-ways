"""Outbound adapters: offline mocks (the default) and plain JSON webhooks.

The engine only needs three calls: messenger.send, crm.upsert and
notifier.notify. If no URL is configured the mock is used, so tests and the
demo never touch the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping


class TransientError(Exception):
    """Worth retrying: timeouts, rate limits, 5xx responses."""


class PermanentError(Exception):
    """Retrying will not help: bad number, rejected request."""


def with_retry(
    call: Callable[[], Any],
    attempts: int,
    base_delay: float,
    sleep: Callable[[float], None],
    on_retry: Callable[[int, float, Exception], None] | None = None,
) -> Any:
    """Run call. Retry TransientError with exponential backoff, then re-raise."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except TransientError as exc:
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            if on_retry:
                on_retry(attempt, delay, exc)
            sleep(delay)
    raise ValueError("attempts must be at least 1")


class MockMessenger:
    """Records sends. Failure can be scripted per recipient."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.permanent_failures: set[str] = set()
        self.transient_failures: dict[str, int] = {}

    def send(self, channel: str, to: str, body: str) -> str:
        if to in self.permanent_failures:
            raise PermanentError(f"{channel} to {to} rejected by provider")
        if self.transient_failures.get(to, 0) > 0:
            self.transient_failures[to] -= 1
            raise TransientError(f"{channel} provider timeout")
        self.sent.append({"channel": channel, "to": to, "body": body})
        return f"mock-{len(self.sent)}"


class MockCRM:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.fail_with: Exception | None = None

    def upsert(self, record: Mapping[str, Any]) -> None:
        if self.fail_with:
            raise self.fail_with
        self.records[record["key"]] = dict(record)


class MockNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.fail_with: Exception | None = None

    def notify(self, text: str) -> None:
        if self.fail_with:
            raise self.fail_with
        self.messages.append(text)


class JsonWebhook:
    """POST JSON to one URL and map HTTP failures onto the two error types."""

    def __init__(self, url: str, token: str | None = None, opener=urllib.request.urlopen,
                 timeout: float = 10.0) -> None:
        self.url, self.token, self.opener, self.timeout = url, token, opener, timeout

    def post(self, payload: Mapping[str, Any]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                raise TransientError(f"HTTP {exc.code}") from exc
            raise PermanentError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TransientError(f"network error: {exc}") from exc


class WebhookMessenger:
    def __init__(self, hook: JsonWebhook) -> None:
        self.hook = hook

    def send(self, channel: str, to: str, body: str) -> str:
        return self.hook.post({"channel": channel, "to": to, "body": body})


class WebhookCRM:
    def __init__(self, hook: JsonWebhook) -> None:
        self.hook = hook

    def upsert(self, record: Mapping[str, Any]) -> None:
        self.hook.post(dict(record))


class WebhookNotifier:
    def __init__(self, hook: JsonWebhook) -> None:
        self.hook = hook

    def notify(self, text: str) -> None:
        self.hook.post({"text": text})


def build_adapters(env: Mapping[str, str]) -> tuple[Any, Any, Any]:
    """Return (messenger, crm, notifier). Each falls back to its mock."""
    token = env.get("GATEWAY_TOKEN") or None

    def pick(name: str, real: Callable[[JsonWebhook], Any], mock: Callable[[], Any]) -> Any:
        url = env.get(name, "").strip()
        return real(JsonWebhook(url, token)) if url else mock()

    return (
        pick("MESSAGING_WEBHOOK_URL", WebhookMessenger, MockMessenger),
        pick("CRM_WEBHOOK_URL", WebhookCRM, MockCRM),
        pick("TEAM_WEBHOOK_URL", WebhookNotifier, MockNotifier),
    )
