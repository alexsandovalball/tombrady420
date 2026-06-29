"""Lightweight SEO route smoke checks for a running Bradycadia deployment.

Usage:
    python scripts/seo_smoke_check.py https://topshelfcaliplug.com
    python scripts/seo_smoke_check.py http://localhost:10000 --product-id 123

The script uses only the Python standard library so it can run on Render shells
or local machines without installing test dependencies.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass
class FetchResult:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def fetch(url: str, *, etag: Optional[str] = None) -> FetchResult:
    headers = {"User-Agent": "BradycadiaSEOCheck/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return FetchResult(
                url=response.geturl(),
                status=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                body=response.read(),
            )
    except urllib.error.HTTPError as exc:
        return FetchResult(
            url=url,
            status=exc.code,
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=exc.read(),
        )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_etag(url: str, label: str) -> None:
    first = fetch(url)
    etag = first.headers.get("etag")
    require(first.status == 200, f"{label}: expected 200, got {first.status}")
    require(bool(etag), f"{label}: missing ETag")
    second = fetch(url, etag=etag)
    require(second.status == 304, f"{label}: expected 304 with If-None-Match, got {second.status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check public SEO routes.")
    parser.add_argument("base_url", help="Site origin, for example https://topshelfcaliplug.com")
    parser.add_argument("--product-id", type=int, default=0, help="Optional product ID to inspect")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    robots = fetch(f"{base}/robots.txt")
    require(robots.status == 200, f"robots.txt status {robots.status}")
    require("Allow: /media/products/" in robots.text, "robots.txt must allow /media/products/")
    require("Disallow: /api/" in robots.text, "robots.txt must block /api/")
    require("Disallow: /webhook" in robots.text, "robots.txt must block /webhook")
    check_etag(f"{base}/robots.txt", "robots.txt")

    sitemap = fetch(f"{base}/sitemap.xml")
    require(sitemap.status == 200, f"sitemap.xml status {sitemap.status}")
    require("<urlset" in sitemap.text and "</urlset>" in sitemap.text, "sitemap.xml is not a sitemap")
    require("/product/" in sitemap.text, "sitemap.xml has no product URLs")
    require("/media/products/" in sitemap.text, "sitemap.xml has no public image URLs")
    check_etag(f"{base}/sitemap.xml", "sitemap.xml")

    if args.product_id:
        product_url = f"{base}/product/{args.product_id}"
        product = fetch(product_url)
        require(product.status in {200, 301, 302}, f"product redirect/page status {product.status}")
        if product.status in {301, 302}:
            location = product.headers.get("location", "")
            require("/product/" in location or "/category/" in location or location == "/", "bad product redirect")
            if location.startswith("/"):
                product = fetch(f"{base}{location}")
            elif location:
                product = fetch(location)
        require(product.status == 200, f"canonical product page status {product.status}")
        product_etag = product.headers.get("etag")
        require(bool(product_etag), "product page missing ETag")
        product_revalidated = fetch(product.url, etag=product_etag)
        require(product_revalidated.status == 304, f"product page ETag expected 304, got {product_revalidated.status}")
        html = product.text
        require("<h1" in html, "product page missing h1")
        require('rel="canonical"' in html, "product page missing canonical")
        require('property="og:image"' in html, "product page missing og:image")
        require("/media/products/" in html, "product page missing public photo media URL")
        media_match = re.search(r"/media/products/\d+/\d+", html)
        if media_match:
            media = fetch(f"{base}{media_match.group(0)}")
            require(media.status in {200, 304}, f"public media status {media.status}")
            require(media.headers.get("content-type", "").startswith("image/"), "public media is not image content")

    print("SEO smoke check passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"SEO smoke check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
