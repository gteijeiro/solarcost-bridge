from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin


SIGN_IN_CSRF_RE = re.compile(r'name="_csrf_token" type="hidden" hidden value="([^"]+)"')


@dataclass(slots=True)
class TotalsPageContext:
    page_url: str
    csrf_token: str
    root_id: str
    session: str
    static: str
    gateway_timezone: str | None
    track_static: list[str]


class TotalsPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.csrf_token: str | None = None
        self.root_attrs: dict[str, str] | None = None
        self.gateway_timezone: str | None = None
        self.track_static: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: (value if value is not None else "") for key, value in attrs}
        if tag == "meta" and attr_map.get("name") == "csrf-token":
            self.csrf_token = attr_map.get("content")
        if tag == "body" and "data-gateway-timezone" in attr_map:
            self.gateway_timezone = attr_map.get("data-gateway-timezone")
        if tag == "div" and "data-phx-main" in attr_map:
            self.root_attrs = attr_map
        if "phx-track-static" in attr_map:
            static_url = attr_map.get("src") or attr_map.get("href")
            if static_url:
                self.track_static.append(static_url)


def extract_sign_in_csrf(html: str) -> str:
    match = SIGN_IN_CSRF_RE.search(html)
    if not match:
        raise ValueError("could not find sign-in CSRF token")
    return match.group(1)


def parse_totals_page(html: str, page_url: str) -> TotalsPageContext:
    parser = TotalsPageParser()
    parser.feed(html)
    if not parser.csrf_token:
        raise ValueError("could not find totals page CSRF token")
    if not parser.root_attrs:
        raise ValueError("totals page does not contain a LiveView root")

    root = parser.root_attrs
    return TotalsPageContext(
        page_url=page_url,
        csrf_token=parser.csrf_token,
        root_id=root["id"],
        session=root["data-phx-session"],
        static=root["data-phx-static"],
        gateway_timezone=parser.gateway_timezone,
        track_static=[urljoin(page_url, item) for item in parser.track_static],
    )
