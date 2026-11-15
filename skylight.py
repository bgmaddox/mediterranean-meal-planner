"""
skylight.py
-----------
A small client for the Skylight Calendar's meal planner.

Skylight has no public API. This talks to the private one its own apps use,
as reverse-engineered by the community; the login flow and the meal routes
follow github.com/chrischall/skylight-mcp, which verifies them against the live
service (see FUTURE_WORK.md for the version used). Expect it to break when
Skylight changes something, and expect the break to show up here first.

Stdlib only. The token pair lives in .skylight/token.json (gitignored, 0600).
The password is never stored: `cli.py skylight-login` asks for it once, and
after that the refresh token keeps the session alive. Skylight rotates the
refresh token on every refresh, so the file is rewritten each time.

Network access goes through a `transport` callable so tests can fake it:
    transport(method, url, headers, body) -> Response
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BASE_URL = "https://app.ourskylight.com"
API = BASE_URL + "/api"
API_VERSION = "2026-05-01"   # without this header some features answer 422
CLIENT_ID = "skylight-mobile"
SCOPE = "everything"
REDIRECT_URI = "https://ourskylight.com/welcome"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DEVICE_PARAMS = {
    "skylight_api_client_device_platform": "web",
    "skylight_api_client_device_name": "unknown",
    "skylight_api_client_device_os_version": "10.15.7",
    "skylight_api_client_device_app_version": "unknown",
    "skylight_api_client_device_hardware": "Macintosh",
    "source": "js-mobile",
}
REFRESH_SKEW_S = 60
TIMEOUT_S = 30


class SkylightError(Exception):
    pass


class NotLoggedIn(SkylightError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""

    def header(self, name: str) -> Optional[str]:
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.body) if self.body else None
        except ValueError:
            raise SkylightError(f"Skylight answered HTTP {self.status} with something that is not JSON")


Transport = Callable[[str, str, dict, Optional[bytes]], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect())


def urllib_transport(method: str, url: str, headers: dict, body: Optional[bytes]) -> Response:
    """Real HTTP. Redirects are returned, never followed: the login reads them."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=TIMEOUT_S) as r:
            return Response(r.status, list(r.headers.items()), r.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, list(e.headers.items()) if e.headers else [], e.read() or b"")
    except urllib.error.URLError as e:
        raise SkylightError(f"Could not reach Skylight: {e.reason}")


# ─────────────────────────────────────────────────────────────────────────────
# Tokens on disk
# ─────────────────────────────────────────────────────────────────────────────

class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> Optional[dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        return data if data.get("access_token") else None

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(tokens, f)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _tokens_from(status: int, data) -> dict:
    if not 200 <= status < 300 or not isinstance(data, dict) or not data.get("access_token"):
        error = data.get("error") if isinstance(data, dict) else None
        raise SkylightError(f"Skylight token request failed (HTTP {status}{', ' + str(error) if error else ''})")
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or "",
        "expires_at": time.time() + float(data.get("expires_in") or 604800),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Login: OAuth2 authorization code + PKCE behind a Rails sign-in form, with an
# emailed one-time code when Skylight asks for one (since 2026-11-11).
# ─────────────────────────────────────────────────────────────────────────────

class _Jar:
    """Cookies for the one auth host. Later Set-Cookie wins; Max-Age=0 deletes."""

    def __init__(self):
        self.cookies: dict[str, str] = {}

    def absorb(self, resp: Response) -> None:
        for raw in resp.all("Set-Cookie"):
            first, _, attrs = raw.partition(";")
            name, _, value = first.strip().partition("=")
            if not name:
                continue
            if re.search(r"max-age\s*=\s*0\b", attrs, re.I) or not value:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    def header(self) -> Optional[str]:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items()) or None


def _csrf(html: str, page: str) -> str:
    for pattern in (
        r"""<[^>]*\bname\s*=\s*["']authenticity_token["'][^>]*\bvalue\s*=\s*["']([^"']+)["']""",
        r"""<[^>]*\bvalue\s*=\s*["']([^"']+)["'][^>]*\bname\s*=\s*["']authenticity_token["']""",
        r"""<meta[^>]*\bname\s*=\s*["']csrf-token["'][^>]*\bcontent\s*=\s*["']([^"']+)["']""",
    ):
        m = re.search(pattern, html, re.I)
        if m:
            return m.group(1)
    raise SkylightError(f"Skylight login failed: no sign-in token on {page}; the login page may have changed.")


def _redirect_path(resp: Response, request_url: str) -> Optional[str]:
    """The same-origin path a redirect points at; None if not a redirect."""
    loc = resp.header("Location")
    if not 300 <= resp.status < 400 or not loc:
        return None
    dest = urllib.parse.urlsplit(urllib.parse.urljoin(request_url, loc))
    if f"{dest.scheme}://{dest.netloc}" != BASE_URL:
        raise SkylightError("Skylight login failed: unexpected redirect off the Skylight site")
    return dest.path


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login(email: str, password: str, ask_code: Callable[[], str],
          transport: Transport = urllib_transport) -> dict:
    """Sign in and return a token dict. `ask_code` is called if Skylight emails
    a one-time code; it returns what the user typed (empty to give up)."""
    jar = _Jar()

    def send(method, url, body=None, extra=None):
        headers = {"User-Agent": USER_AGENT, **(extra or {})}
        if jar.header():
            headers["Cookie"] = jar.header()
        resp = transport(method, url, headers, body)
        jar.absorb(resp)
        return resp

    def form(fields):
        return urllib.parse.urlencode(fields).encode()

    def post_form(path, fields, referer):
        return send("POST", BASE_URL + path, form(fields), {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": BASE_URL, "Referer": BASE_URL + referer})

    # 1. The sign-in page, for its CSRF token and session cookie.
    page = send("GET", BASE_URL + "/auth/session/new")
    if page.status >= 400:
        raise SkylightError(f"Skylight login page answered HTTP {page.status}")
    token = _csrf(page.text(), "/auth/session/new")

    # 2. The password.
    step2 = post_form("/auth/session", {"authenticity_token": token, "email": email,
                                        "password": password}, "/auth/session/new")
    path = _redirect_path(step2, BASE_URL + "/auth/session")
    if path and path.startswith("/auth/mfa_challenge"):
        start = urllib.parse.urljoin(BASE_URL + "/auth/session", step2.header("Location"))
        path = _answer_challenge(send, post_form, start, ask_code)
    if path is None and re.search(r"""\bname\s*=\s*["']password["']""", step2.text(), re.I):
        path = "/auth/session/new"
    if path and path.startswith("/auth/session/new"):
        raise SkylightError("Skylight rejected the email or password "
                            "(or is rate-limiting after repeated attempts).")
    if path != "/auth/session/success":
        raise SkylightError(f"Skylight login stopped at an unexpected step (HTTP {step2.status} "
                            f"{path or ''}); Skylight may have changed its sign-in.")

    # 3. Trade the signed-in session for an authorization code.
    verifier, challenge = _pkce()
    url = BASE_URL + "/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "response_type": "code", "scope": SCOPE,
        "redirect_uri": REDIRECT_URI, "code_challenge": challenge,
        "code_challenge_method": "S256"})
    code = None
    for _ in range(4):
        resp = send("GET", url)
        loc = resp.header("Location")
        if not 300 <= resp.status < 400 or not loc:
            break
        dest = urllib.parse.urljoin(url, loc)
        if dest.split("?")[0] == REDIRECT_URI:
            code = urllib.parse.parse_qs(urllib.parse.urlsplit(dest).query).get("code", [None])[0]
            break
        if not dest.startswith(BASE_URL + "/"):
            raise SkylightError("Skylight login failed: unexpected authorization redirect")
        url = dest
    if not code:
        raise SkylightError("Skylight login failed: no authorization code (the OAuth flow may have changed).")

    # 4. Exchange it for tokens.
    resp = transport("POST", BASE_URL + "/oauth/token", {
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }, form({"grant_type": "authorization_code", "client_id": CLIENT_ID, "scope": SCOPE,
             "skylight_api_client_device_fingerprint": str(uuid.uuid4()), **DEVICE_PARAMS,
             "redirect_uri": REDIRECT_URI, "code": code, "code_verifier": verifier}))
    return _tokens_from(resp.status, resp.json())


def _challenge_form(send, url: str) -> str:
    """Follow redirects to the emailed-code form; return its CSRF token."""
    for _ in range(4):
        resp = send("GET", url)
        path = _redirect_path(resp, url)
        if path is None:
            return _csrf(resp.text(), "/auth/mfa_challenge/new")
        if path.startswith("/auth/session/new"):
            raise SkylightError("Skylight ended the sign-in before the code could be entered; try again.")
        url = urllib.parse.urljoin(url, resp.header("Location"))
    raise SkylightError("Skylight login failed: too many redirects to the code form.")


def _answer_challenge(send, post_form, start_url: str, ask_code, attempts: int = 3) -> str:
    """Skylight has emailed a 6-digit code. Ask for it; return where it led."""
    token = _challenge_form(send, start_url)   # GETting the start URL sends the email
    for _ in range(attempts):
        code = re.sub(r"\s+", "", ask_code() or "")
        if not code:
            raise SkylightError("Sign-in cancelled: no code entered.")
        if not re.fullmatch(r"\d{6}", code):
            print("A Skylight login code is 6 digits.")
            continue
        resp = post_form("/auth/mfa_challenge", {"authenticity_token": token, "code": code},
                         "/auth/mfa_challenge/new")
        path = _redirect_path(resp, BASE_URL + "/auth/mfa_challenge")
        if path and path.startswith("/auth/mfa_challenge"):
            print("Skylight rejected that code; check the newest email.")
            token = _challenge_form(send, BASE_URL + path)
            continue
        return path or ""
    raise SkylightError("Too many wrong codes; run skylight-login again for a new one.")


def refresh(refresh_token: str, transport: Transport = urllib_transport) -> dict:
    resp = transport("POST", BASE_URL + "/oauth/token", {
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }, urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": CLIENT_ID,
                               "refresh_token": refresh_token}).encode())
    if resp.status in (400, 401):
        raise NotLoggedIn("Skylight no longer accepts the saved session; run `cli.py skylight-login`.")
    tokens = _tokens_from(resp.status, resp.json())
    tokens["refresh_token"] = tokens["refresh_token"] or refresh_token
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# The API
# ─────────────────────────────────────────────────────────────────────────────

class Skylight:
    def __init__(self, store: TokenStore, transport: Transport = urllib_transport):
        self.store = store
        self.transport = transport

    def _token(self, force_refresh: bool = False) -> str:
        tokens = self.store.load()
        if not tokens:
            raise NotLoggedIn("Not signed in to Skylight; run `cli.py skylight-login` first.")
        if force_refresh or tokens.get("expires_at", 0) - REFRESH_SKEW_S < time.time():
            if not tokens.get("refresh_token"):
                raise NotLoggedIn("The Skylight session expired; run `cli.py skylight-login`.")
            tokens = refresh(tokens["refresh_token"], self.transport)
            self.store.save(tokens)
        return tokens["access_token"]

    def request(self, method: str, path: str, query: Optional[dict] = None,
                body: Optional[dict] = None):
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._token(force_refresh=attempt == 1)}",
                       "Accept": "application/json", "User-Agent": USER_AGENT,
                       "skylight-api-version": API_VERSION}
            if data is not None:
                headers["Content-Type"] = "application/json"
            resp = self.transport(method, url, headers, data)
            if resp.status == 401 and attempt == 0:
                continue
            break
        if resp.status >= 400:
            raise SkylightError(f"Skylight {method} {path.split('?')[0]} failed: HTTP {resp.status}"
                                f"{_error_text(resp)}")
        return resp.json() if resp.status != 204 else None

    # Frames: one Skylight account can own several devices.
    def frames(self) -> list[dict]:
        return _flatten((self.request("GET", "/frames") or {}).get("data"))

    def meal_categories(self, frame: str) -> list[dict]:
        return _flatten((self.request("GET", f"/frames/{frame}/meals/categories") or {}).get("data"))

    def sittings(self, frame: str, date_min: str, date_max: str) -> list[dict]:
        """Planned meals from date_min up to but NOT including date_max
        (observed live 2026-11-14: a one-day range min == max returns nothing)."""
        doc = self.request("GET", f"/frames/{frame}/meals/sittings",
                           {"date_min": date_min, "date_max": date_max, "include": "meal_category"})
        return [_with_category(s) for s in _rows((doc or {}).get("data"))]

    def create_sitting(self, frame: str, category_id: str, day: str, summary: str,
                       description: str) -> dict:
        """A one-time planned meal. No recipe is linked: summary and description
        carry it, so nothing is added to Skylight's recipe box."""
        doc = self.request("POST", f"/frames/{frame}/meals/sittings", body={
            "meal_category_id": category_id, "date": day, "summary": summary,
            "description": description})
        rows = _rows((doc or {}).get("data"))
        if not rows:
            raise SkylightError("Skylight created a meal but did not say which; re-run the sync to check.")
        return _with_category(rows[0])

    def update_sitting(self, frame: str, sitting_id: str, instance_date: str, fields: dict) -> None:
        self.request("PATCH", f"/frames/{frame}/meals/sittings/{sitting_id}/instances/{instance_date}",
                     body=fields)

    def delete_sitting(self, frame: str, sitting_id: str, instance_date: str) -> None:
        self.request("DELETE", f"/frames/{frame}/meals/sittings/{sitting_id}/instances/{instance_date}")


def _rows(data) -> list[dict]:
    return data if isinstance(data, list) else [data] if isinstance(data, dict) else []


def _flatten(data) -> list[dict]:
    return [{**(r.get("attributes") or {}), "id": str(r.get("id"))} for r in _rows(data)]


def _with_category(r: dict) -> dict:
    flat = {**(r.get("attributes") or {}), "id": str(r.get("id"))}
    cat = ((r.get("relationships") or {}).get("meal_category") or {}).get("data")
    flat["meal_category_id"] = str(cat["id"]) if isinstance(cat, dict) and cat.get("id") else None
    flat["instances"] = [i if isinstance(i, str) else (i or {}).get("date") for i in flat.get("instances") or []]
    return flat


def _error_text(resp: Response) -> str:
    try:
        data = resp.json()
    except SkylightError:
        return ""
    errors = data.get("errors") if isinstance(data, dict) else None
    if not errors:
        return ""
    return f": {json.dumps(errors)[:300]}"


def category_name(c: dict) -> str:
    for key in ("label", "name", "summary", "title"):
        if c.get(key):
            return str(c[key])
    return ""
