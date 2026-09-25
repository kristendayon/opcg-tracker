import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from build_data import parse_yuyutei, variant_label, merge_items, clean_name

# Mimics the structure observed on yuyu-tei.jp/sell/opc/s/op01 (class names are guesses;
# the parser only relies on the card link text, the 円 price and the 在庫 marker).
HTML = """<body><h3>SR Card List</h3>
<div class="card"><a href="/sell/opc/card/op01/10030">OP01-024 SR モンキー・D・ルフィ</a> OP01-024
<h4><a href="/sell/opc/card/op01/10030">モンキー・D・ルフィ</a></h4>
<strong>120 円</strong> 在庫 : ◯</div>
<div class="card"><a href="/sell/opc/card/op01/10085">OP01-067 SR クロコダイル</a>
<h4><a href="/sell/opc/card/op01/10085">クロコダイル</a></h4>
<strong>80 円</strong> 在庫 : 3 点 <del>120 円</del></div>
<div class="card"><a href="/sell/opc/card/op01/10152">OP01-120 P-SEC シャンクス(パラレル)(スーパーパラレル)(刻印なし)</a>
<strong>128,000 円</strong> 在庫 : ×</div>
<div class="card"><a href="/sell/opc/card/op01/10155">- - ドン!!カード(黒文字白背景)</a><strong>30 円</strong></div>
</body>"""

def test_parse():
    r = parse_yuyutei(HTML)
    assert [x["number"] for x in r] == ["OP01-024", "OP01-067", "OP01-120"]
    assert r[0]["price"] == 120 and r[0]["stock"] == 1
    assert r[1]["price"] == 80 and r[1]["stock"] == 3   # struck-through 120 ignored
    assert r[2]["price"] == 128000 and r[2]["stock"] == 0 and r[2]["rarity"] == "P-SEC"

def test_variant():
    assert variant_label("シャンクス(パラレル)(スーパーパラレル)") == "Super Parallel"
    assert variant_label("ゾロ(パラレル)") == "Parallel"
    assert variant_label("ゾロ") == "Normal"


def test_yuyu_only_cards_kept():
    cards = {"OP01-024": {"number": "OP01-024", "name": "Monkey.D.Luffy", "prices": []}}
    items = [
        {"number": "OP01-024", "rarity": "SR", "jp_name": "モンキー・D・ルフィ", "price": 120, "stock": 1, "url": "u1"},
        {"number": "OP99-001", "rarity": "P-SR", "jp_name": "新カード(パラレル)", "price": 900, "stock": 0, "url": "u2"},
        {"number": "OP99-001", "rarity": "SR", "jp_name": "新カード", "price": 100, "stock": 3, "url": "u3"},
    ]
    priced, jp_only = merge_items(cards, "OP99", items)
    assert (priced, jp_only) == (3, 1)
    assert len(cards["OP01-024"]["prices"]) == 1 and "jpOnly" not in cards["OP01-024"]
    new = cards["OP99-001"]
    assert new["jpOnly"] and new["name"] == "新カード" and len(new["prices"]) == 2 and new["set"] == "OP99"
    assert clean_name("シャンクス(パラレル)(スーパーパラレル)(刻印なし)") == "シャンクス"
