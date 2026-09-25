#!/usr/bin/env python3
"""Build web/cards.json: English card data (community optcgjson dataset)
merged with the latest Yuyu-tei selling prices (JPY).

Run:  python scripts/build_data.py                 (all sets)
      python scripts/build_data.py OP01 ST01       (only some sets, for testing)
      python scripts/build_data.py --scrape-only   (only fetch Yuyu-tei prices into data/prices.json;
                                                    run this on your OWN computer if GitHub is blocked)

If Yuyu-tei refuses the request (HTTP 403 from GitHub's servers), the build does NOT fail: it uses the
saved data/prices.json if the repository has one, or publishes the card data without prices.
"""
import json, re, sys, time, datetime, pathlib, argparse
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "cards.json"
SAVED = ROOT / "data" / "prices.json"
UA = {"User-Agent": "opcg-collection-tracker/1.0 (personal hobby project; low-rate)"}
YY_HEADERS = {  # look like an ordinary browser visit
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
    "Referer": "https://yuyu-tei.jp/top/opc",
}
TIME_BUDGET = 8 * 60      # never spend more than 8 minutes scraping
MAX_BLOCKS = 3            # stop after this many 403/429 answers in a row
DB_URL = "https://raw.githubusercontent.com/hugoprudente/optcgjson/main/output/{code}.json"
YY_TOP = "https://yuyu-tei.jp/top/opc"
YY_SET = "https://yuyu-tei.jp/sell/opc/s/{slug}"
DELAY = 1.5  # seconds between Yuyu-tei requests: be polite

NUMBER_RE = re.compile(r"^([A-Z]{1,3}\d{0,2}-\d{3})\s+(\S+)\s+(.+)$")
PRICE_RE = re.compile(r"([\d,]+)\s*円")
STOCK_RE = re.compile(r"在庫\s*[:：]?\s*(\S+)")
HREF_RE = re.compile(r"/sell/opc/card/([^/]+)/(\d+)")


def get(url, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


def fetch_yuyu(session, url):
    """Return (status, html). status: 'ok' | 'missing' (404) | 'blocked' (403/429) | 'error'."""
    for i in range(2):
        try:
            r = session.get(url, timeout=(10, 25))
        except requests.RequestException as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
            time.sleep(2)
            continue
        if r.status_code == 404:
            return "missing", None
        if r.status_code in (403, 429):
            print(f"  ! {url}: HTTP {r.status_code} (blocked)", file=sys.stderr)
            return "blocked", None
        if r.status_code >= 500:
            print(f"  ! {url}: HTTP {r.status_code}", file=sys.stderr)
            time.sleep(2)
            continue
        return "ok", r.text
    return "error", None


def scrape(codes, budget=TIME_BUDGET, session=None, sleep=time.sleep):
    """Fetch price rows for each set page. Returns (rows_by_set, info)."""
    session = session or requests.Session()
    session.headers.update(YY_HEADERS)
    start, blocked_in_row, out = time.time(), 0, {}
    info = {"blocked": False, "timeout": False}
    for code in codes:
        if time.time() - start > budget:
            print(f"  time budget of {budget}s used up - stopping", file=sys.stderr)
            info["timeout"] = True
            break
        status, html = fetch_yuyu(session, YY_SET.format(slug=code.lower()))
        sleep(DELAY)
        if status == "blocked":
            blocked_in_row += 1
            if blocked_in_row >= MAX_BLOCKS:
                print("  Yuyu-tei is refusing requests from this machine - giving up on live prices", file=sys.stderr)
                info["blocked"] = True
                break
            continue
        blocked_in_row = 0
        if status != "ok":
            print(f"  {code}: no Yuyu-tei page")
            continue
        items = parse_yuyutei(html)
        out[code] = items
        print(f"  {code}: {len(items)} price rows")
    return out, info


def parse_yuyutei(html):
    """Return list of {number, rarity, jp_name, price, stock, url} from one set page."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(["del", "s", "strike"]):  # struck-through old prices
        t.decompose()
    seen, items = set(), []
    for a in soup.find_all("a", href=HREF_RE):
        m = NUMBER_RE.match(" ".join(a.get_text(" ", strip=True).split()))
        if not m:
            continue
        href = a["href"]
        if href in seen:
            continue
        number, rarity, jp_name = m.groups()
        # climb to the smallest ancestor that holds only this card's link(s)
        box = a
        while box.parent is not None and box.parent.name not in ("body", "html"):
            hrefs = {x["href"] for x in box.parent.find_all("a", href=HREF_RE)}
            if hrefs - {href}:
                break
            box = box.parent
        text = " ".join(box.get_text(" ", strip=True).split())
        pm = PRICE_RE.search(text)
        if not pm:
            continue
        sm = STOCK_RE.search(text)
        stock = 0
        if sm:
            s = sm.group(1)
            stock = int(s) if s.isdigit() else (1 if s == "◯" else 0)
        seen.add(href)
        items.append({
            "number": number, "rarity": rarity, "jp_name": jp_name.strip(),
            "price": int(pm.group(1).replace(",", "")), "stock": stock,
            "url": "https://yuyu-tei.jp" + href if href.startswith("/") else href,
        })
    return items


def variant_label(jp_name):
    if "スーパーパラレル" in jp_name: return "Super Parallel"
    if "パラレル" in jp_name: return "Parallel"
    if "刻印なし" in jp_name: return "No stamp"
    return "Normal"


def clean_name(jp_name):
    """Yuyu-tei appends (パラレル) / (刻印なし) to variant names; strip them for a base name."""
    return re.sub(r"\((?:スーパー)?パラレル\)|\(刻印なし\)", "", jp_name).strip()


def merge_items(cards, code, items):
    """Attach Yuyu-tei price rows to cards. A Yuyu-tei card that is missing from the English
    dataset (brand-new set, Japan-only promo...) is kept as a 'jpOnly' record with its Japanese
    name so the app can tell the user 'listed on Yuyu-tei, English details missing'."""
    priced = jp_only = 0
    for it in items:
        card = cards.get(it["number"])
        if not card:
            card = cards[it["number"]] = {
                "number": it["number"], "name": clean_name(it["jp_name"]), "type": "", "color": [],
                "cost": None, "power": None, "life": None, "counter": None, "rarity": it["rarity"],
                "set": code, "image": "", "prices": [], "jpOnly": True}
            jp_only += 1
        elif card.get("jpOnly") and "パラレル" not in it["jp_name"] and card["name"] != clean_name(it["jp_name"]):
            card["name"] = clean_name(it["jp_name"])
        card["prices"].append({
            "variant": variant_label(it["jp_name"]), "rarity": it["rarity"],
            "jp_name": it["jp_name"], "price_jpy": it["price"],
            "stock": it["stock"], "url": it["url"],
        })
        priced += 1
    return priced, jp_only


def load_db(codes):
    cards = {}
    for code in codes:
        txt = get(DB_URL.format(code=code))
        if not txt:
            continue
        for c in json.loads(txt)["data"]["cards"]:
            if c.get("isParallel"):
                continue  # parallels share stats with the base card
            cards[c["number"]] = {
                "number": c["number"], "name": c["name"], "type": c["cardClass"],
                "color": c["color"], "cost": c["cost"], "power": c["power"],
                "life": c["life"], "counter": c["counter"], "rarity": c["rarity"],
                "set": code, "image": c["imageUrl"], "prices": [],
            }
    return cards


def all_codes():
    return ([f"OP{i:02d}" for i in range(1, 21)] + [f"ST{i:02d}" for i in range(1, 41)]
            + [f"EB{i:02d}" for i in range(1, 11)] + [f"PRB{i:02d}" for i in range(1, 6)])


def save_prices(rows_by_set):
    SAVED.parent.mkdir(exist_ok=True)
    SAVED.write_text(json.dumps({"date": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "sets": rows_by_set},
                                ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_saved():
    try:
        d = json.loads(SAVED.read_text(encoding="utf-8"))
        return d.get("sets", {}), d.get("date", "")
    except (OSError, ValueError):
        return {}, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--scrape-only", action="store_true")
    args = ap.parse_args()
    codes = [a.upper() for a in args.codes] or all_codes()
    full = len(codes) > 5

    if args.scrape_only:
        rows, info = scrape(codes)
        total = sum(len(v) for v in rows.values())
        if total < 500 and full:
            sys.exit(f"Only {total} price rows fetched (blocked={info['blocked']}). data/prices.json NOT written.")
        save_prices(rows)
        print(f"Saved {total} price rows to {SAVED}. Upload data/prices.json to your GitHub repository.")
        return

    cards = load_db(codes)
    print(f"English data: {len(cards)} cards")
    if full and len(cards) < 100:
        sys.exit(f"Only {len(cards)} English cards downloaded - refusing to publish an empty card list.")
    rows, info = scrape(codes)
    total = sum(len(v) for v in rows.values())
    if total >= 500 or (not full and total > 0):
        status, date = "live", datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
        if full:
            save_prices(rows)   # handy when run on your own computer; ignored by the website build
    else:
        rows, date = load_saved()
        if rows:
            status = "saved"
            print(f"::warning::Yuyu-tei live prices unavailable - using saved prices from {date}")
        else:
            status = "none"
            print("::warning::Yuyu-tei live prices unavailable and no saved prices - publishing card data without prices")
    priced = jp_only = 0
    for code, items in rows.items():
        p, j = merge_items(cards, code, items)
        priced += p
        jp_only += j
    print(f"Price status: {status}. Price rows merged: {priced}. Cards on Yuyu-tei but missing from English data: {jp_only}")
    out = {"updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
           "source": "Prices: yuyu-tei.jp. Card data: optcgjson (community).",
           "prices": {"status": status, "date": date, "blocked": info["blocked"]},
           "cards": cards}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT} ({len(cards)} cards, {priced} price rows)")


if __name__ == "__main__":
    main()
