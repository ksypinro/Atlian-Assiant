"""Browser sessions (design 10.8): a persistent Playwright profile that a HUMAN signs in to. The client never types credentials and never solves a CAPTCHA.

``BrowserSession`` is the small surface the backend needs; ``PlaywrightSession`` implements it (lazy import), tests use ``FakeBrowserSession``. The guards live here, in
one place: a login or bot-challenge page is reported as "a human must act", and ``fill`` refuses anything that looks like a credential field.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import anyio

from ...core.errors import AuthenticationError, BackendUnavailableError, ConfigurationError, PolicyDeniedError

LOGIN_URL = re.compile(r"(id\.atlassian\.com/(login|saml|signup)|/login(\?|$|/)|/plugins/servlet/saml/auth|accounts\.google\.com|login\.microsoftonline\.com)", re.I)
LOGIN_HTML = re.compile(r"""type=["']password["']|name=["']password["']|data-testid=["']login-form["']""", re.I)
CHALLENGE_HTML = re.compile(r"g-recaptcha|h-captcha|hcaptcha|cf-challenge|cf-turnstile|captcha-container|are you a robot|verify you are human", re.I)
CREDENTIAL_FIELD = re.compile(r"(passw|passcode|otp|one-time|2fa|mfa|totp|secret|token|api[-_ ]?key|credential|type=.?password|\bpin\b|security[-_ ]?code|verification[-_ ]?code)", re.I)


PERSONAL_PROFILES = re.compile(r"(google/chrome|google-chrome|chromium|brave-?software|microsoft edge|microsoft-edge|mozilla/firefox|\.mozilla|firefox|safari|vivaldi|opera)", re.I)


def check_profile_dir(profile_dir: Path) -> Path:
    """SEC-08: the backend gets its OWN profile directory. A person's everyday browser profile (cookies for every site they use) is refused."""
    p = Path(profile_dir).expanduser()
    if PERSONAL_PROFILES.search(str(p)):
        raise ConfigurationError(f"{p} looks like a personal browser profile", hint="Use a dedicated directory (e.g. ~/.atlian-assiant/browser-profile): the client must never share cookies with your everyday browser.")
    return p


@dataclass(frozen=True)
class Snapshot:
    url: str
    status: int
    html: str


@dataclass(frozen=True)
class Download:
    data: bytes
    filename: str | None = None
    mime_type: str | None = None


@runtime_checkable
class BrowserSession(Protocol):
    async def open(self, url: str) -> Snapshot: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def click(self, selector: str) -> Snapshot: ...
    async def attach(self, selector: str, name: str, data: bytes) -> None: ...
    async def download(self, url: str) -> Download: ...
    async def screenshot(self, path: Path) -> Path: ...
    async def aclose(self) -> None: ...


def check_page(snapshot: Snapshot) -> Snapshot:
    """Turn 'we are not signed in' / 'a bot challenge appeared' into a precise instruction for a human. Nothing is typed or solved here."""
    if LOGIN_URL.search(snapshot.url) or (snapshot.status in (401, 403) and LOGIN_HTML.search(snapshot.html)) or LOGIN_HTML.search(snapshot.html):
        raise AuthenticationError(f"the browser profile is not signed in ({snapshot.url})",
                                  hint="A human must sign in (SSO / MFA) once in the persistent browser profile; the client never types credentials.")
    if CHALLENGE_HTML.search(snapshot.html):
        raise BackendUnavailableError(f"a bot challenge (CAPTCHA) blocked the page ({snapshot.url})",
                                      hint="A human must solve it in the browser profile; the client never solves CAPTCHAs. Use the REST or MCP backend meanwhile.")
    return snapshot


def guard_fill(selector: str, value: str) -> None:
    """Refuse to type into credential-looking inputs (SEC-08). The check looks at the selector the caller chose, never at the value."""
    if CREDENTIAL_FIELD.search(selector):
        raise PolicyDeniedError(f"refusing to type into {selector!r}: it looks like a credential field",
                                hint="The browser backend never enters passwords, one-time codes or tokens; a human signs in once in the persistent profile.")


class PlaywrightSession:
    """Persistent-profile Playwright session. Concurrency is 1: one page, one operation at a time."""

    def __init__(self, profile_dir: Path, *, headless: bool = True, timeout_ms: int = 30_000, playwright_module: Any = None) -> None:
        self.profile_dir, self.headless, self.timeout_ms, self._pw_module = check_profile_dir(profile_dir), headless, timeout_ms, playwright_module
        self._lock = anyio.Lock()
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None

    async def _ensure(self) -> Any:
        if self._page is not None:
            return self._page
        module = self._pw_module
        if module is None:
            try:
                module = importlib.import_module("playwright.async_api")
            except ImportError as exc:
                raise BackendUnavailableError("Playwright is not installed", hint="pip install 'atlian-assiant[browser]' && playwright install chromium") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile_dir.chmod(0o700)                                    # the profile holds a signed-in session: owner only
        self._pw = await module.async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(str(self.profile_dir), headless=self.headless, accept_downloads=True)
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self._page

    async def open(self, url: str) -> Snapshot:
        async with self._lock:
            page = await self._ensure()
            resp = await page.goto(url, wait_until="domcontentloaded")
            return Snapshot(page.url, resp.status if resp else 0, await page.content())

    async def fill(self, selector: str, value: str) -> None:
        guard_fill(selector, value)
        async with self._lock:
            await (await self._ensure()).fill(selector, value)

    async def click(self, selector: str) -> Snapshot:
        async with self._lock:
            page = await self._ensure()
            await page.click(selector)
            await page.wait_for_load_state("domcontentloaded")
            return Snapshot(page.url, 200, await page.content())

    async def attach(self, selector: str, name: str, data: bytes) -> None:
        async with self._lock:
            page = await self._ensure()
            await page.set_input_files(selector, files=[{"name": Path(name).name, "mimeType": "application/octet-stream", "buffer": data}])

    async def download(self, url: str) -> Download:
        async with self._lock:
            page = await self._ensure()
            async with page.expect_download() as info:
                await page.evaluate("u => { window.location.href = u }", url)
            dl = await info.value
            path = await dl.path()
            return Download(Path(path).read_bytes(), dl.suggested_filename)

    async def screenshot(self, path: Path) -> Path:
        async with self._lock:
            page = await self._ensure()
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path), full_page=True)
            return path

    async def aclose(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._pw is not None:
            await self._pw.stop()
        self._context = self._pw = self._page = None
