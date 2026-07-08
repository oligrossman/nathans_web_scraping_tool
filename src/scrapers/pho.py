"""Scraper for Pho restaurant website menu (PDF download + pdfplumber parse)."""

from __future__ import annotations

import io
import re

import pdfplumber
import requests

from .base import BaseScraper, MenuItem, parse_price

_PDF_URL = (
    "https://content.phocafe.co.uk/wp-content/uploads/2026/06/Pho_Main-Menu_web.pdf"
)
_TRAILING_PRICE = re.compile(r"(\d+\.\d{2})\s*$")
_SECTION_HINTS = (
    "starters",
    "sides",
    "classics",
    "salads",
    "noodles",
    "rice",
    "kids",
    "desserts",
    "drinks",
    "beer",
    "wine",
    "cocktails",
    "tea",
    "coffee",
    "soft drinks",
)


class PhoScraper(BaseScraper):
    site_name = "Pho (Birmingham - Grand Central, Restaurant)"
    base_url = _PDF_URL

    def scrape(self) -> list[MenuItem]:
        response = requests.get(self.base_url, timeout=30)
        response.raise_for_status()
        return self._parse_pdf(response.content)

    def _parse_pdf(self, content: bytes) -> list[MenuItem]:
        items: list[MenuItem] = []
        seen: set[tuple[str, str | None]] = set()
        category: str | None = None
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("Scan here"):
                        continue
                    lowered = line.lower()
                    if self._looks_like_section(line, lowered):
                        category = line.rstrip(":").title()
                        continue
                    match = _TRAILING_PRICE.search(line)
                    if not match:
                        continue
                    price = parse_price(match.group(1))
                    name = line[: match.start()].strip(" -|")
                    if "|" in name:
                        name = name.split("|", 1)[0].strip()
                    if not name or price is None or len(name) < 3:
                        continue
                    if "kcal" in name.lower():
                        name = re.sub(r"\s+\d+\s*kcal.*$", "", name, flags=re.I).strip()
                    if ":" in name and len(name) > 40:
                        continue
                    key = (name, category)
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(MenuItem(name=name, price=price, category=category))
        return items

    @staticmethod
    def _looks_like_section(line: str, lowered: str) -> bool:
        if len(line) > 60 or "kcal" in lowered or "£" in line:
            return False
        if line.endswith(":"):
            return True
        return any(hint in lowered for hint in _SECTION_HINTS) and (
            line == line.upper() or line.istitle()
        )
