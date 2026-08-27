"""Materiał gotowy do wgrania do CM360: JEDEN zip na wymiar, nazwany samym wymiarem.

Życzenie użytkownika: „docelowo potrzebujemy by każdy wymiar zapakowany był w osobny zip
nazwany tylko wymiarem, np. 120x600 — to można zawsze wyciągnąć z nazwy folderu albo
wymiaru". Dostawy przychodzą w trzech kształtach i tylko jeden z nich jest już taki:

  * paczka per źródło z GOTOWYMI zipami w środku (`…_kv1_programmatic.zip` → `300x250.zip`)
    — bierzemy ten zip bez ruszania zawartości;
  * paczka per źródło z folderami wymiarów (`…_gdn.zip` → `240x400/index.html`)
    — pakujemy folder, obcinając prefiks, żeby `index.html` był w korzeniu;
  * zip z JEDNYM banerem (`160x600_gdn.zip`) albo luźne pliki w folderze
    (`GDN/linia1/banner_160x600/…`) — pierwszy bierzemy jak jest, drugi pakujemy.

Nic tu nie sięga do sieci ani do CM360: wejściem jest ścieżka do zipa zlecenia i jednostka
z `parse_zip`, wyjściem `(nazwa, bajty)`. Dzięki temu writer nie musi wiedzieć, jak
dostawca poukładał paczkę.
"""
import io
import os
import zipfile

import parse_zip          # te same reguły śmieci i obcinania korzenia, co przy analizie


def _normalize(data):
    """Zip materiału w kształcie, jakiego oczekuje CM: bez śmieci systemowych i bez
    zbędnego folderu-opakowania (`500x400/500x400.html` -> `500x400.html`).

    Dostawcy pakują to na oba sposoby w tej samej paczce, a różnica decyduje o tym, czy
    CM znajdzie plik główny kreacji — więc wyrównujemy zawsze, także dla zipów, których
    zawartości nie ruszamy z innych powodów.
    """
    src = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in src.namelist()
             if not n.endswith("/") and not parse_zip._is_junk(n)]
    if not names:
        return data
    # Wspólny folder wiodący jest tu opakowaniem ZAWSZE — także gdy nazywa się jak
    # wymiar (`500x400/500x400.html`), bo w zipie materiału wymiar znamy z nazwy pliku.
    # To inna decyzja niż w `parse_zip._strip_root`, gdzie folder z wymiarem jest jedynym
    # nośnikiem tej informacji i obcięcie go gubiłoby wymiar dostawy.
    prefix = ""
    while (all("/" in n[len(prefix):] for n in names)
           and len({n[len(prefix):].split("/")[0] for n in names}) == 1):
        prefix += names[0][len(prefix):].split("/")[0] + "/"
    if not prefix and len(names) == len(src.namelist()):
        return data                          # nic do poprawienia
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:                      # podfoldery (`images/`) zostają
            out.writestr(n[len(prefix):], src.read(n))
    return buf.getvalue()


def _zip_from(src, names, strip=""):
    """Nowy zip z wybranych wpisów `src`, z obciętym prefiksem `strip`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            rel = n[len(strip):] if strip and n.startswith(strip) else os.path.basename(n)
            if rel:
                out.writestr(rel, src.read(n))
    return buf.getvalue()


def _entries(zf, prefix):
    return [n for n in zf.namelist()
            if n.startswith(prefix) and not n.endswith("/")]


def unit_asset(zip_path, unit):
    """(nazwa, bajty) materiału jednej jednostki — zawsze `{wymiar}.zip`.

    `zip_path` to paczka zlecenia; gdy zlecenie ma KILKA paczek, jednostka nosi `_zip`
    ze swoją własną i ta wygrywa — dwie paczki mogą mieć w środku identyczne ścieżki.

    Rzuca `ValueError`, gdy w paczce nie ma czego wgrać (np. jednostka bez wymiaru albo
    ścieżka, której już nie ma) — cicha pusta kreacja byłaby gorsza niż jawny błąd.
    """
    zip_path = unit.get("_zip") or zip_path
    dim = unit.get("dimension")
    sp = (unit.get("source_path") or "").replace("\\", "/")
    if not sp:
        raise ValueError(f"jednostka bez ścieżki w paczce: {unit}")
    name = f"{dim}.zip" if dim else (os.path.basename(sp) or "asset.zip")

    with zipfile.ZipFile(zip_path) as z:
        # jednostka z PACZKI per źródło: source_path = "<zip w środku>/<wymiar>"
        if unit.get("package") and "/" in sp:
            outer, inner_dim = sp.rsplit("/", 1)
            with z.open(outer) as fh:
                inner = zipfile.ZipFile(io.BytesIO(fh.read()))
                ready = [n for n in inner.namelist()
                         if os.path.basename(n).lower() == f"{inner_dim}.zip".lower()]
                if ready:                                   # dostawca już to zapakował
                    return name, _normalize(inner.read(ready[0]))
                names = _entries(inner, inner_dim + "/")
                if not names:
                    raise ValueError(f"brak plików wymiaru {inner_dim} w {outer}")
                return name, _zip_from(inner, names, strip=inner_dim + "/")

        # zip z jednym banerem — zawartości nie zmieniamy, tylko wyrównujemy kształt
        if sp.lower().endswith(".zip"):
            return name, _normalize(z.read(sp))

        # luźne pliki w folderze
        names = _entries(z, sp.rstrip("/") + "/")
        if not names:
            raise ValueError(f"brak plików pod ścieżką {sp}")
        return name, _zip_from(z, names, strip=sp.rstrip("/") + "/")


def asset_plan(zip_path, units):
    """[(nazwa, rozmiar w bajtach, wymiar)] — do pokazania w planie zapisu bez uploadu."""
    out = []
    for u in units:
        try:
            n, data = unit_asset(zip_path, u)
            out.append({"name": n, "bytes": len(data), "dimension": u.get("dimension"),
                        "error": None})
        except Exception as e:
            out.append({"name": None, "bytes": 0, "dimension": u.get("dimension"),
                        "error": str(e)})
    return out
