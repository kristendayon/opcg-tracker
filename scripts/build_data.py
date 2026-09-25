#!/usr/bin/env python3
"""Build web/cards.json: English card data (community optcgjson dataset)
merged with the latest Yuyu-tei selling prices (JPY).

Run:  python scripts/build_data.py            (all sets)
      python scripts/build_data.py OP01 ST01  (only some sets, for testing)
"""
import json, re, sys, time, datetime, pathlib
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "cards.json"
UA = {"User-Agent": "opcg-collection-tracker/1.0 (personal hobby project; low-rate)"}
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


def main():
    codes = [a.upper() for a in sys.argv[1:]]
    if not codes:
        codes = ([f"OP{i:02d}" for i in range(1, 21)] + [f"ST{i:02d}" for i in range(1, 41)]
                 + [f"EB{i:02d}" for i in range(1, 11)] + [f"PRB{i:02d}" for i in range(1, 6)])
    cards = load_db(codes)
    print(f"English data: {len(cards)} cards")

    priced = jp_only = 0
    for code in codes:
        html = get(YY_SET.format(slug=code.lower()))
        time.sleep(DELAY)
        if not html:
            print(f"  {code}: no Yuyu-tei page")
            continue
        items = parse_yuyutei(html)
        p, j = merge_items(cards, code, items)
        priced += p
        jp_only += j
        print(f"  {code}: {len(items)} price rows ({j} not in English data)")
    print(f"Cards on Yuyu-tei but missing from English data: {jp_only}")
    if len(codes) > 5 and priced < 500:
        sys.exit(f"Only {priced} prices parsed - Yuyu-tei markup probably changed. Not overwriting cards.json.")
    out = {"updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
           "source": "Prices: yuyu-tei.jp. Card data: optcgjson (community).",
           "cards": cards}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT} ({len(cards)} cards, {priced} price rows)")


if __name__ == "__main__":
    main()
