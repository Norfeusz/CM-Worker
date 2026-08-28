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
import repack

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
      sorted(gdn), sorted([(s, d) for s in ("kv1", "kv3") for d in GDN_DIMS]))
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

print("\nKILKA PACZEK w jednym zleceniu — scalanie z zachowaniem źródła paczki:")
z_gdn = _outer({"banner_300x250/index.html": "<html></html>",
                "banner_160x600/index.html": "<html></html>"})
z_prog = _outer({"banner_970x250/index.html": "<html></html>"})
merged = parse_zip.merge_parsed([
    {"parsed": parse_zip.parse(z_gdn), "source": "GDN", "path": z_gdn, "name": "hh_gdn.zip"},
    {"parsed": parse_zip.parse(z_prog), "source": "Programmatic", "path": z_prog,
     "name": "hh_programmatic.zip"}])
check("jednostki obu paczek w jednej strukturze", merged["n_units"], 3)
check("każda jednostka wie, z którego ŹRÓDŁA pochodzi",
      sorted({(u["group"], u["dimension"]) for u in merged["units"]}),
      [("GDN", "160x600"), ("GDN", "300x250"), ("Programmatic", "970x250")])
check("...i z którego PLIKU (dwie paczki mogą mieć te same ścieżki w środku)",
      sorted({u["_zipName"] for u in merged["units"]}),
      ["hh_gdn.zip", "hh_programmatic.zip"])
check("źródła paczek stają się grupami", [g["name"] for g in merged["groups"]],
      ["GDN", "Programmatic"])
check("wymiary zsumowane", merged["dimensions"], ["160x600", "300x250", "970x250"])
check("podsumowanie paczek dla UI",
      [(p["name"], p["source"], p["units"]) for p in merged["packages"]],
      [("hh_gdn.zip", "GDN", 2), ("hh_programmatic.zip", "Programmatic", 1)])

# podział WEWNĄTRZ paczki jest dokładniejszy niż przypisanie całego pliku
z_mixed = _outer({"GDN/300x250/index.html": "<html></html>",
                  "WP/970x300/index.html": "<html></html>"})
mixed = parse_zip.merge_parsed([
    {"parsed": parse_zip.parse(z_mixed), "source": "Programmatic", "path": z_mixed,
     "name": "wszystko.zip"}])
check("foldery w środku paczki wygrywają nad przypisaniem pliku",
      sorted({u["group"] for u in mixed["units"]}), ["GDN", "WP"])
check("...więc materiał GDN nie wchodzi do zlecenia programmatica",
      any(u["group"] == "Programmatic" for u in mixed["units"]), False)

check("ostrzeżenia niosą nazwę paczki, której dotyczą",
      all(w.startswith("hh_gdn.zip: ") or w.startswith("hh_programmatic.zip: ")
          for w in merged["warnings"]), True)
one = parse_zip.parse(z_gdn)
check("jedna paczka bez przypisanego źródła -> struktura bez zmian",
      parse_zip.merge_parsed([{"parsed": one, "path": z_gdn, "name": "x.zip"}]) is one, True)

# materiał do wgrania bierze się z WŁASNEJ paczki jednostki
u_prog = [u for u in merged["units"] if u["dimension"] == "970x250"][0]
name_p, data_p = repack.unit_asset(z_gdn, u_prog)     # celowo zła paczka w argumencie
check("upload bierze plik z paczki jednostki, nie z pierwszej z listy",
      (name_p, len(data_p) > 0), ("970x250.zip", True))

print("\nREPAKOWANIE — jeden zip na wymiar, HTML w korzeniu (wymóg usera i CM):")


def _banner_zip(dim, wrap=None, junk=False):
    """Zip jednego banera; `wrap` = folder-opakowanie, `junk` = śmieci macOS."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        p = f"{wrap}/" if wrap else ""
        z.writestr(f"{p}{dim}.html", "<html></html>")
        z.writestr(f"{p}images/x.png", b"x")
        if junk:
            z.writestr("__MACOSX/._x", b"j")
            z.writestr(f"{p}.DS_Store", b"j")
    return buf.getvalue()


def _pkg(dims, ready=False):
    """Paczka per źródło: foldery wymiarów, opcjonalnie z GOTOWYMI zipami obok."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for d in dims:
            z.writestr(f"{d}/index.html", "<html></html>")
            z.writestr(f"{d}/img.png", b"x")
            if ready:
                z.writestr(f"{d}.zip", _banner_zip(d, wrap=d))
    return buf.getvalue()


# 1) paczka per źródło z gotowymi zipami — bierzemy je, ale wyrównujemy kształt.
# Dwa wymiary, bo paczką (a nie jednym banerem) jest zip z KILKOMA wymiarami w środku.
p_ready = _outer({"KV1_x/pack_kv1_gdn.zip": _pkg(["300x250", "160x600"], ready=True)})
u = [x for x in parse_zip.parse(p_ready)["units"] if x["dimension"] == "300x250"][0]
name, data = repack.unit_asset(p_ready, u)
inner = zipfile.ZipFile(io.BytesIO(data)).namelist()
check("nazwa zipa to sam wymiar", name, "300x250.zip")
check("gotowy zip dostawcy: folder-opakowanie obcięte",
      sorted(inner), ["300x250.html", "images/x.png"])

# 2) paczka per źródło BEZ gotowych zipów — pakujemy folder wymiaru
p_dirs = _outer({"KV1_x/pack_kv1_gdn.zip": _pkg(["300x250", "160x600"])})
u2 = [x for x in parse_zip.parse(p_dirs)["units"] if x["dimension"] == "160x600"][0]
n2, d2 = repack.unit_asset(p_dirs, u2)
check("folder wymiaru spakowany, prefiks obcięty",
      (n2, sorted(zipfile.ZipFile(io.BytesIO(d2)).namelist())),
      ("160x600.zip", ["img.png", "index.html"]))
check("materiał drugiego wymiaru NIE wchodzi do tego zipa",
      any("300x250" in x for x in zipfile.ZipFile(io.BytesIO(d2)).namelist()), False)

# 3) zip = jeden baner, z opakowaniem i śmieciami macOS
p_one = _outer({"out/500x400.zip": _banner_zip("500x400", wrap="500x400", junk=True)})
u3 = parse_zip.parse(p_one)["units"][0]
n3, d3 = repack.unit_asset(p_one, u3)
check("zip jednego banera: opakowanie obcięte, śmieci wyrzucone",
      (n3, sorted(zipfile.ZipFile(io.BytesIO(d3)).namelist())),
      ("500x400.zip", ["500x400.html", "images/x.png"]))

# 4) luźne pliki w folderze
p_loose = _outer({"GDN/banner_300x250/index.html": "<html></html>",
                  "GDN/banner_300x250/images/a.png": b"x",
                  "GDN/banner_160x600/index.html": "<html></html>"})
u4 = [x for x in parse_zip.parse(p_loose)["units"] if x["dimension"] == "300x250"][0]
n4, d4 = repack.unit_asset(p_loose, u4)
check("luźne pliki spakowane, HTML w korzeniu",
      (n4, sorted(zipfile.ZipFile(io.BytesIO(d4)).namelist())),
      ("300x250.zip", ["images/a.png", "index.html"]))

# 5) brak materiału to jawny błąd, nie cicha pusta kreacja
try:
    repack.unit_asset(p_loose, {"dimension": "999x999", "source_path": "GDN/nie_ma"})
    check("brak plików -> błąd", "przeszło", "ValueError")
except ValueError:
    check("brak plików -> jawny błąd, nie pusty zip", True, True)
check("plan pokazuje rozmiary bez uploadu",
      [(a["dimension"], a["bytes"] > 0, a["error"]) for a in
       repack.asset_plan(p_loose, parse_zip.parse(p_loose)["units"])],
      [("160x600", True, None), ("300x250", True, None)])

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
check("`KV1_NNW paczki` -> kv1 (po cyfrze stoi `_`, nie granica słowa)",
      parse_zip._set_label("KV1_NNW paczki z reformatami"), "kv1")
check("`KV 3 coś` -> kv3", parse_zip._set_label("KV 3 coś"), "kv3")
check("`KV10_x` -> kv10 (nie kv1)", parse_zip._set_label("KV10_x"), "kv10")
check("zwykły folder nie jest zestawem", parse_zip._set_label("Screening"), None)
check("wymiar nie jest zestawem", parse_zip._set_label("300x250"), None)

print("\nWARIANT Z NAZWY PLIKU (`file_tag`) — paczka Mety z realnego zlecenia NNW:")
# Zgłoszone arkuszem klienta: z 14 plików na zestaw parser robił 4 jednostki, bo klucz
# nie zawierał ogona nazwy pliku. 20 z 28 adów przepadało bez śladu.
check("ogon od wymiaru w dół", parse_zip._file_tag("1080x1080-a.png", "1080x1080"),
      "1080x1080-a")
check("prefiks produktowy odpada",
      parse_zip._file_tag("mBank-uniqa_META_nnw_1200x1200_karuzela-4.jpg", "1200x1200"),
      "1200x1200_karuzela-4")
check("sam wymiar to nie wariant", parse_zip._file_tag("1200x628.png", "1200x628"), None)
check("wymiar z folderu, nie z nazwy pliku -> brak ogona",
      parse_zip._file_tag("index.html", "300x250"), None)
check("bez wymiaru nie ma czego liczyć", parse_zip._file_tag("cokolwiek.png", None), None)

META = _outer({
    "kv1-meta/1080x1080-a.png": b"x", "kv1-meta/1080x1080-b.png": b"x",
    "kv1-meta/1080x1080-kv1.mp4": b"x", "kv1-meta/1200x628.png": b"x",
    "kv3-meta/1080x1080-a.png": b"x", "kv3-meta/1080x1080-kv3.mp4": b"x",
})
rmeta = parse_zip.parse(META)
check("każdy wariant pliku to OSOBNA jednostka", len(rmeta["units"]), 6)
check("...z ogonem nazwy i typem",
      sorted((u["set_index"], u["file_tag"] or "", u["type"]) for u in rmeta["units"]),
      [("kv1", "", "image"), ("kv1", "1080x1080-a", "image"),
       ("kv1", "1080x1080-b", "image"), ("kv1", "1080x1080-kv1", "video"),
       ("kv3", "1080x1080-a", "image"), ("kv3", "1080x1080-kv3", "video")])

# karty karuzeli w PŁASKIEJ paczce zagnieżdżonej: wymiar jest w nazwie pliku razem
# z numerem karty, więc bez ogona cztery karty zwijały się w jedną jednostkę
_kar = io.BytesIO()
with zipfile.ZipFile(_kar, "w") as z:
    for d in ("1200x1200", "1080x1920"):
        for c in (1, 2, 3, 4):
            z.writestr(f"KARUZELA jpg/mBank-uniqa_META_nnw_{d}_karuzela-{c}.jpg", b"x")
rkar = parse_zip.parse(_outer({"KARUZELA jpg (2).zip": _kar.getvalue()}))
check("każda karta karuzeli to osobna jednostka", len(rkar["units"]), 8)
check("...nazwana wymiarem z numerem karty",
      sorted(u["file_tag"] for u in rkar["units"])[:3],
      ["1080x1920_karuzela-1", "1080x1920_karuzela-2", "1080x1920_karuzela-3"])
# a paczka z FOLDERAMI wymiarów nadal daje jedną jednostkę na wymiar — pliki jednego
# banera (index.html + images/) muszą zostać razem
check("paczka z folderami wymiarów nadal zwija baner w jedną jednostkę",
      len(parse_zip.parse(_outer({"p/x_gdn.zip": _inner(GDN_DIMS)}))["units"]),
      len(GDN_DIMS))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
