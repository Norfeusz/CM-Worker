"""Testy parsera zipów na paczkach budowanych w locie (bez plików klienta w repo).

Skupione na kształtach, które realnie przyszły od klienta i które parser mylił:
zagnieżdżone PACZKI per źródło (`…_kv1_gdn.zip`) kontra zagnieżdżone JEDNE banery
(`160x600_gdn.zip`), oraz foldery zestawów (`KV1_…/`, `linia2/`).
"""
import io
import os
import sys
import tempfile
import zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


def _inner(dims):
    """Zagnieżdżona paczka: po jednym banerze HTML5 na każdy wymiar."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for d in dims:
            z.writestr(f"{d}/index.html", "<html></html>")
            z.writestr(f"{d}/img.png", b"x")
        z.writestr("preview.html", "<html></html>")
    return buf.getvalue()


def _outer(entries):
    """entries: {ścieżka w zipie: bytes} -> ścieżka do pliku .zip na dysku."""
    path = os.path.join(tempfile.mkdtemp(), "paczka.zip")
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


GDN_DIMS = ["240x400", "250x360", "930x180", "980x120"]
AFI_DIMS = ["300x250", "970x200"]

print("paczki per ŹRÓDŁO w zagnieżdżonych zipach + foldery KV (realne zgłoszenie):")
kv = _outer({
    "KV1_NNW paczki z reformatami/mbank_nnw_kv1_gdn.zip": _inner(GDN_DIMS),
    "KV1_NNW paczki z reformatami/mbank_nnw_kv1_afiliacja.zip": _inner(AFI_DIMS),
    "KV3_NNW paczki z reformatami/mbank_nnw_kv3_gdn.zip": _inner(GDN_DIMS),
    "KV3_NNW paczki z reformatami/mbank_nnw_kv3_afiliacja.zip": _inner(AFI_DIMS),
})
p = parse_zip.parse(kv)
# Przed poprawką każda zagnieżdżona paczka dawała JEDNĄ jednostkę z pierwszym
# napotkanym wymiarem — z 4 wymiarów GDN zostawał jeden, a do drzewa trafiał wymiar
# z paczki innego źródła.
check("każdy wymiar z każdej paczki to osobna jednostka",
      p["n_units"], 2 * (len(GDN_DIMS) + len(AFI_DIMS)))
check("źródło czytane z nazwy PACZKI, nie z całego zipa",
      [g["name"] for g in p["groups"]], ["GDN", "afiliacja"])
check("...z rozpoznanym źródłem tam, gdzie da się je rozpoznać",
      [g["source_hint"] for g in p["groups"]], ["GDN", None])
gdn = {(u["set_index"], u["dimension"]) for u in p["units"] if u["group"] == "GDN"}
check("GDN ma SWOJE wymiary w obu zestawach",
      sorted(gdn), sorted([(s, d) for s in ("KV1", "KV3") for d in GDN_DIMS]))
check("...i żadnego wymiaru z paczki afiliacji",
      {d for s, d in gdn} & set(AFI_DIMS), set())
check("folder KV to ZESTAW, nie wariant (nie może udawać folderu strony docelowej)",
      p["variants"], [])
check("preview.html w paczce nie jest materiałem",
      [u for u in p["units"] if u["dimension"] is None], [])

print("\nzagnieżdżony zip = JEDEN baner (wymiar w nazwie zipa) — bez zmian:")
per_size = _outer({
    "out/160x600_gdn 1.zip": _inner(["160x600"]),
    "out/300x250_gdn 1.zip": _inner(["300x250"]),
})
ps = parse_zip.parse(per_size)
check("jedna jednostka na zip", ps["n_units"], 2)
check("wymiary z nazw zipów", ps["dimensions"], ["160x600", "300x250"])
# regresja: nazwa takiego zipa niesie WYMIAR, nie źródło — wyciąganie z niej grupy
# robiło grupy o nazwach wymiarów i dublowało folder źródła (`gdn 1` obok `GDN`)
check("nazwa takiego zipa NIE tworzy grupy", ps["groups"], [])
check("...ani nie zostaje po niej ślad w jednostkach",
      {u.get("package") for u in ps["units"]}, {None})

print("\nfoldery zestawów `linia{N}` (ustalone: jedno LP, rozróżnienie na adzie):")
sets = _outer({
    "GDN/linia1/banner_300x250/index.html": "<html></html>",
    "GDN/linia1/banner_160x600/index.html": "<html></html>",
    "GDN/linia2/banner_300x250/index.html": "<html></html>",
    "GDN/linia2/banner_160x600/index.html": "<html></html>",
})
st = parse_zip.parse(sets)
check("każdy zestaw osobno, nic się nie zwija",
      sorted((u["set_index"], u["dimension"]) for u in st["units"]),
      [("1", "160x600"), ("1", "300x250"), ("2", "160x600"), ("2", "300x250")])
# `GDN/` jest tu wspólnym opakowaniem całego zipa, więc parser je obcina — zostaje sam
# folder zestawu, a ten wariantem nie jest (inaczej trafiłby do nazw adów i na listę
# kandydatów na folder strony docelowej)
check("`linia{N}` nie jest wariantem", st["variants"], [])
check("...także na poziomie jednostek",
      {u.get("variant") for u in st["units"]}, {None})

print("\nNIEZNANY folder obok znanych — realne zgłoszenie (GDN/ + Programmatic/ + WP/):")
# WP nie jest w słowniku źródeł, więc wpadało do „resztek”, a resztki należą do źródła
# głównego — czyli materiały WP zostały zakodowane jako programmatic (dwa placementy
# o tej samej nazwie, z obcymi wymiarami). Paczka rozdzielona po źródłach nie ma resztek.
wp = _outer({
    "Linia 3 HH/GDN/300x250/index.html": "<html></html>",
    "Linia 3 HH/Programmatic/970x250/index.html": "<html></html>",
    "Linia 3 HH/WP/970x300/index.html": "<html></html>",
})
pw = parse_zip.parse(wp)
check("nieznany folder jest GRUPĄ, nie resztkami",
      [g["name"] for g in pw["groups"]], ["GDN", "Programmatic", "WP"])
check("...i żadna jednostka nie zostaje bez grupy",
      {u.get("group") for u in pw["units"]}, {"GDN", "Programmatic", "WP"})
check("wymiar WP nie należy do programmatica",
      sorted(u["dimension"] for u in pw["units"] if u["group"] == "Programmatic"),
      ["970x250"])
# paczka BEZ podziału po źródłach nie zaczyna nagle robić grup z folderów wymiarów
plain = _outer({"out/300x250/index.html": "<html></html>",
                "out/970x250/index.html": "<html></html>"})
check("foldery wymiarów nie są grupami", parse_zip.parse(plain)["groups"], [])
# ani z folderów zestawów
setsonly = _outer({"p/linia1/300x250/index.html": "<html></html>",
                   "p/linia2/300x250/index.html": "<html></html>"})
check("foldery zestawów nie są grupami", parse_zip.parse(setsonly)["groups"], [])

print("\nMAILING — linki z index.html (jednostką jest wysyłka, nie baner):")
MAIL_HTML = """<html><body>
  <a href="https://www.mbank.pl/">logo</a>
  <a href="#">CTA (placeholder agencji)</a>
  <a href='https://www.mbank.pl/regulamin'>regulamin</a>
  <a href="https://www.mbank.pl/">to samo co logo</a>
  <a href="mailto:kontakt@mbank.pl">napisz</a>
  <img src="img.png">
</body></html>"""
mail = _outer({"index.html": MAIL_HTML, "img.png": b"x", "logo.png": b"y"})
pm = parse_zip.parse(mail)
check("jedna wysyłka na plik HTML", [m["file"] for m in pm["mailings"]], ["index.html"])
check("linki http, unikalne, w kolejności z dokumentu",
      pm["mailings"][0]["links"],
      ["https://www.mbank.pl/", "https://www.mbank.pl/regulamin"])
# `#` i `mailto:` nie są adresami do trafficowania, ale w realnej wysyłce CTA bywa
# placeholderem `#` — dlatego są raportowane, a nie milcząco gubione
check("linki bez adresu zgłoszone, nie zgubione",
      pm["mailings"][0]["skippedLinks"], ["#", "mailto:kontakt@mbank.pl"])
check("...i widać to w ostrzeżeniach",
      any("bez adresu do trafficowania" in w for w in pm["warnings"]), True)

multi = _outer({"mail1/index.html": MAIL_HTML, "mail2/index.html":
                '<a href="https://www.mbank.pl/x">x</a>'})
check("kilka wysyłek w paczce = kilka plików index",
      [m["file"] for m in parse_zip.parse(multi)["mailings"]],
      ["mail1/index.html", "mail2/index.html"])

# Paczka banerów HTML5 z `index.html` per wymiar też zostanie wypisana — `mailings` to
# tylko DANE, używa ich wyłącznie źródło Mailing, więc dla GDN nie zmienia niczego.
banners = _outer({f"{d}/index.html": '<a href="https://x.pl/">clicktag</a>'
                  for d in ("300x250", "160x600", "300x600", "728x90")})
check("indeksy banerów też trafiają do `mailings` (to dane, nie decyzja)",
      len(parse_zip.parse(banners)["mailings"]), 4)
# ...a HTML-e nie-indeksowe w liczbie większej niż 3 nie są już nawet czytane
htmls = _outer({f"banner_{d}.html": '<a href="https://x.pl/">c</a>'
                for d in ("300x250", "160x600", "300x600", "728x90")})
check("wiele nie-indeksowych HTML-i = paczka banerów, nie wysyłki",
      parse_zip.parse(htmls)["mailings"], [])

print("\netykieta zestawu z nazwy folderu:")
check("`linia2` -> 2", parse_zip._set_label("linia2"), "2")
check("`KV1_NNW paczki` -> KV1 (po cyfrze stoi `_`, nie granica słowa)",
      parse_zip._set_label("KV1_NNW paczki z reformatami"), "KV1")
check("`KV 3 coś` -> KV3", parse_zip._set_label("KV 3 coś"), "KV3")
check("`KV10_x` -> KV10 (nie KV1)", parse_zip._set_label("KV10_x"), "KV10")
check("zwykły folder nie jest zestawem", parse_zip._set_label("Screening"), None)
check("wymiar nie jest zestawem", parse_zip._set_label("300x250"), None)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
