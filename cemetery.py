#!/usr/bin/env python3
"""Dead Link Cemetery: find dead links, dig up Wayback Machine copies,
and let a LOCAL model write an epitaph for each one."""

import argparse
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser

import requests

OLLAMA = "http://localhost:11434"
HEADERS = {"User-Agent": "Mozilla/5.0 (DeadLinkCemetery hobby project)"}
CACHE_FILE = "cemetery_cache.json"
# These codes mean "the site refuses robots", so we cannot tell if it is dead.
BLOCKED_CODES = {401, 403, 429, 999}


# ---------- reading links ----------
class BookmarkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            a = dict(attrs)
            href = a.get("href") or ""
            if href.startswith("http"):
                added = a.get("add_date") or ""
                self._cur = {
                    "url": href,
                    "title": "",
                    "added": int(added) if added.isdigit() else None,
                }

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["title"] += data

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._cur is not None:
            self._cur["title"] = self._cur["title"].strip()
            self.items.append(self._cur)
            self._cur = None


def load_links(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    if path.lower().endswith((".html", ".htm")):
        parser = BookmarkParser()
        parser.feed(text)
        items = parser.items
    else:
        items = [
            {"url": line.strip(), "title": "", "added": None}
            for line in text.splitlines()
            if line.strip().startswith("http")
        ]
    seen, unique = set(), []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique


# ---------- step 1: which links are dead? ----------
def check_link(item):
    url = item["url"]
    result = {"dead": False, "reason": ""}
    try:
        r = requests.get(url, headers=HEADERS, timeout=10,
                         allow_redirects=True, stream=True)
        code = r.status_code
        r.close()
        if code in (404, 410):
            result = {"dead": True, "reason": f"the page was deleted (error {code})"}
        elif code in BLOCKED_CODES:
            pass
        elif code >= 400:
            result = {"dead": True, "reason": f"the server kept failing (error {code})"}
    except requests.exceptions.SSLError:
        result = {"dead": True, "reason": "its security certificate expired or broke"}
    except requests.exceptions.Timeout:
        result = {"dead": True, "reason": "it stopped answering (timed out)"}
    except requests.exceptions.ConnectionError:
        result = {"dead": True, "reason": "the address stopped existing (domain gone or server off)"}
    except requests.exceptions.RequestException:
        result = {"dead": True, "reason": "it could not be reached at all"}
    return {**item, **result}


# ---------- step 2: dig in the Wayback Machine ----------
def wayback_capture(url, which):
    limit = 1 if which == "first" else -1
    try:
        r = requests.get(
            "https://web.archive.org/cdx/search/cdx",
            params={"url": url, "output": "json", "limit": limit,
                    "filter": "statuscode:200", "fl": "timestamp"},
            headers=HEADERS, timeout=40,
        )
        rows = [row for row in r.json() if row and row[0] != "timestamp"]
        if rows:
            return rows[-1][0]
    except Exception:
        pass
    return None


def snapshot_text(url, ts):
    try:
        r = requests.get(f"https://web.archive.org/web/{ts}id_/{url}",
                         headers=HEADERS, timeout=40)
        if "html" not in r.headers.get("content-type", "").lower():
            return "", ""
        raw = r.text
    except Exception:
        return "", ""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return title[:150], text[:1200]


def year_of(ts):
    return ts[:4] if ts else None


# ---------- step 3: the local model writes the epitaph ----------
def write_epitaph(model, name, url, born, died, reason, text):
    prompt = f"""You write short, gentle, slightly spooky epitaphs for dead websites.
Use ONLY the facts below. Never invent facts, names, or years.

Facts:
- Name: {name}
- Address: {url}
- Born: {born}
- Died: {died}
- Cause of death (measured by a program): {reason}
- What the old page said: {text or 'nothing could be recovered, so this site is a mystery'}

Write exactly 2 short lines.
Line 1: what this site seems to have been, based only on the facts.
Line 2: a dry, funny or tender farewell.
No emoji, no hashtags, no quotation marks, no extra explanation."""
    try:
        r = requests.post(
            f"{OLLAMA}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.8, "num_predict": 120}},
            timeout=300,
        )
        out = r.json()["response"].strip().replace("*", "").replace('"', "")
        return out or "Here lies a site too quiet to describe."
    except Exception as e:
        print(f"   (model problem: {e})")
        return "Here lies a site. The AI was asleep, so this stone stays plain."


# ---------- cache so reruns are fast ----------
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


# ---------- the graveyard page ----------
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Dead Link Cemetery</title>
<style>
body{margin:0;background:radial-gradient(ellipse at top,#241a33,#0b0810 70%);color:#e8e0f0;
font-family:Georgia,'Times New Roman',serif;min-height:100vh}
header{text-align:center;padding:48px 16px 8px}
h1{font-size:2.4rem;margin:0;letter-spacing:.05em}
header p{color:#b9a9d0;font-style:italic}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
gap:28px;max-width:1100px;margin:30px auto;padding:0 20px}
.stone{background:linear-gradient(#6d6a78,#4a4753);border-radius:110px 110px 10px 10px;
padding:46px 20px 26px;text-align:center;box-shadow:0 0 22px rgba(255,150,30,.18);
border:2px solid #85818f}
.rip{letter-spacing:.3em;font-size:.8rem;color:#d9d4e2}
.name{font-weight:bold;margin:8px 0 2px;word-break:break-word;color:#fff}
.years{color:#ffb347;font-size:1.05rem;margin-bottom:10px}
.epi{font-style:italic;line-height:1.5;margin:10px 0;color:#f3eefb}
.cause{font-size:.78rem;color:#d3cce0;margin-top:12px}
.stone a{color:#ffb347;font-size:.8rem}
footer{text-align:center;color:#8d7fa3;padding:30px 16px 50px;font-size:.85rem}
</style></head><body>
<header><h1>The Dead Link Cemetery</h1>
<p>__COUNT__ pages of my internet past, laid to rest.</p></header>
<div class="grid">__CARDS__</div>
<footer>Exhumed on my own laptop with the open model __MODEL__. No cloud AI was harmed.</footer>
</body></html>"""


def build_page(graves, model):
    cards = []
    for g in graves:
        link = ""
        if g.get("archive"):
            link = f'<div><a href="{html.escape(g["archive"])}" target="_blank" rel="noopener">visit the ghost</a></div>'
        epi = html.escape(g["epitaph"]).replace("\n", "<br>")
        cards.append(
            '<div class="stone"><div class="rip">R.I.P.</div>'
            f'<div class="name">{html.escape(g["name"])}</div>'
            f'<div class="years">{html.escape(str(g["born"]))} &ndash; {html.escape(str(g["died"]))}</div>'
            f'<div class="epi">{epi}</div>'
            f'<div class="cause">Cause of death: {html.escape(g["reason"])}</div>{link}</div>'
        )
    return (PAGE.replace("__CARDS__", "\n".join(cards))
                .replace("__COUNT__", str(len(graves)))
                .replace("__MODEL__", html.escape(model)))


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Dead Link Cemetery")
    ap.add_argument("file", help="bookmarks.html or a text file with one URL per line")
    ap.add_argument("--model", default="gemma3:4b", help="exact name from `ollama list`")
    ap.add_argument("--limit", type=int, default=0, help="only use the first N links (0 = all)")
    ap.add_argument("--out", default="tombstones.html")
    args = ap.parse_args()

    # Is Ollama awake, and does it have the model?
    try:
        tags = requests.get(f"{OLLAMA}/api/tags", timeout=5).json()
        names = [m["name"] for m in tags.get("models", [])]
    except Exception:
        sys.exit("Ollama is not running. Open the Ollama app (or run: ollama serve), then try again.")
    if args.model not in names and f"{args.model}:latest" not in names:
        sys.exit(f"Model '{args.model}' not found. You have: {', '.join(names) or 'nothing'}.\n"
                 f"Fix: run `ollama pull {args.model}` or use --model with one of those names.")

    if not os.path.exists(args.file):
        sys.exit(f"Cannot find '{args.file}'. Is it in this folder?")
    links = load_links(args.file)
    if args.limit:
        links = links[:args.limit]
    if not links:
        sys.exit("No http links found in that file.")

    print(f"Step 1/3: checking {len(links)} links for signs of life...")
    with ThreadPoolExecutor(max_workers=10) as pool:
        checked = list(pool.map(check_link, links))
    dead = [c for c in checked if c["dead"]]
    print(f"   Found {len(dead)} dead and {len(checked) - len(dead)} alive.")
    if not dead:
        sys.exit("No dead links found. Try the sample urls.txt, or raise --limit.")

    print("Step 2/3 and 3/3: digging in the Wayback Machine and writing epitaphs...")
    cache = load_cache()
    graves = []
    for n, d in enumerate(dead, 1):
        url = d["url"]
        print(f"[{n}/{len(dead)}] {url}")
        if url in cache:
            graves.append(cache[url])
            print("   (done before, using saved result)")
            continue
        first = wayback_capture(url, "first")
        time.sleep(1)
        last = wayback_capture(url, "last")
        time.sleep(1)
        old_title, old_text = ("", "")
        if last:
            old_title, old_text = snapshot_text(url, last)
            time.sleep(1)
        born = year_of(first)
        if not born and d["added"]:
            try:
                born = str(datetime.fromtimestamp(d["added"]).year)
            except Exception:
                born = None
        born = born or "unknown"
        died = year_of(last) or "unknown"
        name = d["title"] or old_title or url
        epitaph = write_epitaph(args.model, name, url, born, died, d["reason"], old_text)
        grave = {
            "name": name, "url": url, "born": born, "died": died,
            "reason": d["reason"], "epitaph": epitaph,
            "archive": f"https://web.archive.org/web/{last}/{url}" if last else "",
        }
        graves.append(grave)
        cache[url] = grave
        save_cache(cache)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_page(graves, args.model))
    print(f"\nDone! Open this file in your browser:\n   {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()