#!/usr/bin/env python3
"""Fetch plain text from article URLs (no browser). Research-only."""
from __future__ import annotations
import argparse, re, sys
from html import unescape
import requests
from bs4 import BeautifulSoup

UA={"User-Agent":"Mozilla/5.0 (compatible; GrokBotSales/1.0; research)"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--max-chars", type=int, default=2500)
    args=ap.parse_args()
    for url in args.urls:
        print(f"\n=== {url}")
        try:
            r=requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            soup=BeautifulSoup(r.text, "lxml")
            for t in soup(["script","style","nav","footer","aside"]):
                t.decompose()
            art=soup.find("article") or soup.find("main") or soup.body
            text=re.sub(r"\s+"," ", unescape(art.get_text(" ", strip=True) if art else ""))
            print(text[:args.max_chars])
        except Exception as e:
            print(f"[err] {e}", file=sys.stderr)
if __name__=="__main__":
    main()
