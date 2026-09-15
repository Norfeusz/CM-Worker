"""Które konto CM360 jest aktywne w tym uruchomieniu.

JEDNO miejsce, z którego reszta kodu pyta „na jakim koncie pracujemy”. Wybór robi
zmienna `CM_ENV` (`test` domyślnie, `prod` świadomie), a zapisy na produkcji wymagają
DODATKOWO `CM_PROD_WRITES=1` — patrz `_writes` w `config/environments.json`.

Świadomie bez importu czegokolwiek z CM360: to czysty odczyt configu, żeby dało się go
testować bez sieci i tokenu, a `cm_auth` mógł go użyć jeszcze przed zbudowaniem klienta.
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE, "config", "environments.json")

DEFAULT_ENV = "test"          # brak CM_ENV = konto testowe; produkcja tylko na życzenie
_cache = {}


def _load():
    if not _cache:
        with open(ENV_PATH, encoding="utf-8") as f:
            _cache.update(json.load(f)["environments"])
    return _cache


def env_name():
    """Nazwa aktywnego środowiska. Nieznana wartość NIE cofa się po cichu do testu —
    literówka w `CM_ENV` musi boleć od razu, a nie wyjść dopiero przy zapisie."""
    name = (os.environ.get("CM_ENV") or DEFAULT_ENV).strip().lower()
    if name not in _load():
        raise RuntimeError(
            f"CM_ENV={name!r} nie istnieje w config/environments.json "
            f"(dostępne: {sorted(_load())}).")
    return name


def current(name=None):
    """Konfiguracja aktywnego środowiska (kopia — nikt nie mutuje cache'u po drodze)."""
    return dict(_load()[name or env_name()])


def profile_id(name=None):
    return current(name)["profileId"]


def advertiser_ids(name=None):
    """Zbiór dozwolonych advertiserów albo None = wszyscy advertiserzy TEGO profilu.

    None jest wartością produkcji (decyzja usera): tam advertiser bierze się z linku,
    a jest ich kilkanaście, więc lista byłaby tylko duplikatem `advertiser_map.json`,
    który i tak trzeba utrzymywać. Oddzielenie kont zapewnia allowlista PROFILU.
    """
    ids = current(name).get("advertiserIds")
    return None if ids is None else set(ids)


def writes_allowed(name=None):
    """(bool, powód) — czy TO środowisko w ogóle dopuszcza zapisy.

    Drugi bezpiecznik, niezależny od `service(read_only=False)`: produkcja startuje jako
    tylko-do-odczytu i odblokowuje się jawną zmienną, żeby żadne „kliknąłem nie ten
    przycisk” nie sięgnęło konta klienta.
    """
    env = env_name() if name is None else name
    conf = current(env)
    if conf.get("writes"):
        return True, ""
    if os.environ.get("CM_PROD_WRITES") == "1":
        return True, f"{env}: zapisy odblokowane przez CM_PROD_WRITES=1"
    return False, (f"środowisko {env} ({conf.get('label')}) jest TYLKO DO ODCZYTU. "
                   f"Zapis wymaga świadomego ustawienia CM_PROD_WRITES=1.")


def site_name(source_site, name=None):
    """Nazwa Site tego źródła NA TYM koncie (te same źródła nazywają się różnie)."""
    over = current(name).get("siteOverrides") or {}
    return over.get(source_site, source_site)


def describe(name=None):
    """Krótki opis dla UI i logów — user musi widzieć, na czym pracuje."""
    env = env_name() if name is None else name
    conf = current(env)
    ok, why = writes_allowed(env)
    ids = conf.get("advertiserIds")
    return {"env": env, "label": conf.get("label"), "profileId": conf["profileId"],
            "advertisers": "wszyscy tego profilu" if ids is None else ids,
            "writes": ok, "writesNote": why, "isProd": env != "test"}


if __name__ == "__main__":
    import pprint
    pprint.pprint(describe())
