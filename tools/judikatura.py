"""Stáhne metadata soudních rozhodnutí a sestaví z nich rejstřík podle paragrafů.

Ministerstvo spravedlnosti zveřejňuje anonymizovaná rozhodnutí na veřejném REST API, členěná po
dnech. Každé rozhodnutí u sebe nese, která ustanovení kterých předpisů zmiňuje — a právě to je
tady to cenné: dá se z toho sestavit rejstřík, ve kterém u konkrétního paragrafu vidíš rozhodnutí,
která ho vykládají.

Tři vrstvy, protože všechno naráz se do repozitáře nevejde:

* `judikatura/<předpis>/<§>.md` — rejstřík, to podstatné, verzované
* `judikatura/metadata/RRRR-MM.jsonl.gz` — všechna metadata, komprimovaná
* plné texty **nikdy** — mají dohromady přes 13 GB; skript je stáhne na vyžádání do cache

    python3 tools/judikatura.py --od-roku 2020      # metadata za všechny roky
    python3 tools/judikatura.py --rejstrik          # jen přestaví rejstřík z metadat
    python3 tools/judikatura.py --text ECLI:CZ:...  # plný text jednoho rozhodnutí
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
ZAKONY = KOREN / "zakony"
SMLOUVY = KOREN / "smlouvy"
JUDIKATURA = KOREN / "judikatura"
METADATA = JUDIKATURA / "metadata"
CACHE = KOREN / ".cache"
STAV = CACHE / "stav-judikatura.json"

API = "https://rozhodnuti.justice.cz/api/opendata"
TEXT_API = "https://rozhodnuti.justice.cz/api/finaldoc"
TIMEOUT = 90
SOUBEH = 3
PAUZA = 0.2

# "§ 2235 odst. 1 z. č. 89/2012 Sb." -> (2235, 89/2012). Druh předpisu se zkracuje několika
# způsoby a brát jen "z. č." znamenalo přijít o pětinu zmínek — 771 tisíc vyhlášek a 295 tisíc
# nařízení vlády. Rok musí mít čtyři číslice: soudy píšou i "z. č. 111/94 Sb.", což je rok 1994,
# a doplňovat století za ně by byla hádanka.
USTANOVENI = re.compile(
    r"§\s*(\d+)(\w*)[^§]*?(?:z|zák|vyhl|nař\.?\s*vl|nař|opatř|sděl)\.\s*č\.\s*(\d+/\d{4})\s*Sb"
)

# Nadpis ustanovení v markdownu předpisu — kotva, na kterou míří wikilink z rejstříku.
NADPIS = re.compile(r"^#{1,6} (.+?)\s*$", re.M)

tisk = threading.Lock()
zamek_stavu = threading.Lock()


def json_get(url: str, pokusy: int = 3) -> dict | list:
    for pokus in range(pokusy):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
            if pokus == pokusy - 1:
                raise
            time.sleep(2 ** pokus)
    return {}


def dny_roku(rok: int) -> list[str]:
    """Cesty ke dnům, ve kterých ten rok něco vyšlo."""
    cesty = []
    for mesic in json_get(f"{API}/{rok}"):
        for den in json_get(mesic["odkaz"]):
            if den.get("pocet"):
                cesty.append(den["odkaz"])
    return cesty


def stahni_den(url: str) -> list[dict]:
    """Všechny stránky jednoho dne. Stránky se číslují od nuly a pageSize server ignoruje,
    takže je vždy po stu."""
    polozky: list[dict] = []
    strana = 0
    while True:
        time.sleep(PAUZA)
        d = json_get(f"{url}?page={strana}")
        polozky.extend(d.get("items", []))
        if strana + 1 >= d.get("totalPages", 1):
            return polozky
        strana += 1


# Jméno soudce, které API vrací v poli `autor`. Katalog otevřených dat sadu označuje jako
# obsahující osobní údaje, a CC0 řeší autorské právo, ne GDPR. K hledání judikatury podle
# paragrafu jméno netřeba, tak se nezapisuje.
OSOBNI_UDAJE = ("autor",)


def bez_osobnich_udaju(polozka: dict) -> dict:
    return {k: v for k, v in polozka.items() if k not in OSOBNI_UDAJE}


# Zdroj posílá u části rozhodnutí literál „null“ nebo „<nezadán>“ místo prázdné hodnoty.
# Bez ošetření se to propíše do rejstříku a navíc rozbije řazení podle data.
PRAZDNE = {"null", "none", "<nezadán>", "<nezadan>", "-", ""}


def hodnota(zaznam: dict, klic: str) -> str:
    v = zaznam.get(klic)
    text = "" if v is None else str(v).strip()
    return "" if text.lower() in PRAZDNE else text


def uloz_mesic(rok: int, mesic: int, polozky: list[dict]) -> Path:
    METADATA.mkdir(parents=True, exist_ok=True)
    cil = METADATA / f"{rok}-{mesic:02d}.jsonl.gz"
    with gzip.open(cil, "wt", encoding="utf-8") as f:
        for p in sorted(polozky, key=lambda x: (hodnota(x, "datumZverejneni"), hodnota(x, "ecli"))):
            f.write(json.dumps(bez_osobnich_udaju(p), ensure_ascii=False) + "\n")
    return cil


def stahni_metadata(od_roku: int, do_roku: int | None) -> None:
    stav = json.loads(STAV.read_text(encoding="utf-8")) if STAV.exists() else {}
    roky = [r["rok"] for r in json_get(API) if r["rok"] >= od_roku and (not do_roku or r["rok"] <= do_roku)]

    for rok in roky:
        for mesic_info in json_get(f"{API}/{rok}"):
            mesic = mesic_info["mesic"]
            klic = f"{rok}-{mesic:02d}"
            cil = METADATA / f"{klic}.jsonl.gz"

            # Uzavřený měsíc se nemění; jen ten poslední se ještě doplňuje.
            if stav.get(klic, {}).get("pocet") == mesic_info["pocet"] and cil.exists():
                continue

            dny = [d["odkaz"] for d in json_get(mesic_info["odkaz"]) if d.get("pocet")]
            polozky: list[dict] = []
            with ThreadPoolExecutor(max_workers=SOUBEH) as bazen:
                for cast in bazen.map(stahni_den, dny):
                    polozky.extend(cast)

            uloz_mesic(rok, mesic, polozky)
            with zamek_stavu:
                stav[klic] = {"pocet": len(polozky), "ohlaseno": mesic_info["pocet"]}
            STAV.write_text(json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
            with tisk:
                print(f"  {klic}: {len(polozky):,} rozhodnutí", flush=True)


def nazev_predpisu(citace: str) -> str:
    """`89/2012` -> název souboru zákona v zakony/, aby šel wikilink."""
    m = re.match(r"(\d+\w*)/(\d{4})", citace)
    return f"{m.group(1)}-{m.group(2)}" if m else citace.replace("/", "-")


def nacti_platnost() -> dict:
    """Co je zrušené, podle tools/platnost.py — aby rejstřík nevydával judikaturu ke zrušenému
    předpisu za výklad živého práva."""
    cesta = KOREN / ".zmeny" / "platnost.json"
    return json.loads(cesta.read_text(encoding="utf-8")) if cesta.exists() else {}


def zname_predpisy() -> dict[str, Path]:
    """Předpisy, které v repozitáři skutečně jsou. Rejstřík se staví jen k nim — odkaz na text,
    který tu není, je k ničemu, a filtr zároveň zahodí nesmysly vzniklé při čtení citace
    z textu rozhodnutí (`0/0 Sb.` a podobně)."""
    return {c.stem: c for c in list(ZAKONY.rglob("*.md")) + list(SMLOUVY.rglob("*.md"))}


def kotva_ustanoveni(cesta: Path, paragraf: str, _cache: dict = {}) -> str:
    """Nadpis, na který smí wikilink mířit, nebo prázdno.

    Soudy citují „§ 89" i u předpisů, které paragrafy nemají — Ústava má články — a citovat
    mohou i ustanovení, které pozdější novela zrušila. Odkaz na neexistující kotvu vypadá
    v Obsidianu jako funkční, dokud na něj někdo neklikne, tak se radši ověří."""
    if cesta not in _cache:
        _cache[cesta] = set(NADPIS.findall(cesta.read_text(encoding="utf-8")))
    nadpisy = _cache[cesta]

    for varianta in (f"§ {paragraf}", f"Čl. {paragraf}", f"Článek {paragraf}", f"čl. {paragraf}"):
        if varianta in nadpisy:
            return varianta
    return ""


def postav_rejstrik(min_rozhodnuti: int) -> None:
    """Z metadat sestaví soubor na každý paragraf, na který soudy odkazují."""
    zruseno = nacti_platnost()
    znamé = zname_predpisy()
    podle_paragrafu: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for soubor in sorted(METADATA.glob("*.jsonl.gz")):
        with gzip.open(soubor, "rt", encoding="utf-8") as f:
            for radek in f:
                r = json.loads(radek)
                videno = set()
                for u in r.get("zminenaUstanoveni") or []:
                    m = USTANOVENI.search(u)
                    if not m:
                        continue
                    klic = (m.group(3), m.group(1) + m.group(2))
                    if klic in videno:
                        continue
                    videno.add(klic)
                    podle_paragrafu[klic].append(r)

    vybrane: dict[tuple[str, str], tuple[list[dict], str]] = {}
    bez_predpisu = bez_kotvy = 0
    for (predpis, paragraf), rozhodnuti in podle_paragrafu.items():
        if len(rozhodnuti) < min_rozhodnuti:
            continue
        cesta = znamé.get(nazev_predpisu(predpis))
        if cesta is None:
            bez_predpisu += 1
            continue
        kotva = kotva_ustanoveni(cesta, paragraf)
        if not kotva:
            bez_kotvy += 1
        vybrane[(predpis, paragraf)] = (rozhodnuti, kotva)

    if bez_predpisu:
        print(f"přeskočeno {bez_predpisu:,} paragrafů — předpis v repozitáři není")
    if bez_kotvy:
        print(f"{bez_kotvy:,} paragrafů bez odkazu na text — ustanovení v platném znění není")
    print(f"paragrafů s odkazem: {len(podle_paragrafu):,}, z toho s ≥{min_rozhodnuti} rozhodnutími: {len(vybrane):,}")

    for (predpis, paragraf), (rozhodnuti, kotva) in sorted(vybrane.items()):
        slozka = JUDIKATURA / nazev_predpisu(predpis)
        slozka.mkdir(parents=True, exist_ok=True)

        rozhodnuti.sort(key=lambda r: hodnota(r, "datumVydani"), reverse=True)
        klicova = defaultdict(int)
        for r in rozhodnuti:
            for k in r.get("klicovaSlova") or []:
                klicova[k] += 1

        zaznam = zruseno.get(f"{predpis} Sb.")
        radky = [
            "---",
            f"predpis: {predpis} Sb.",
            f"paragraf: {paragraf}",
            f"rozhodnuti: {len(rozhodnuti)}",
        ]
        if zaznam:
            radky.append(f"zruseno_k: {zaznam['k']}")
        radky += [
            "tags:",
            "  - judikatura",
            f"  - predpis/{nazev_predpisu(predpis)}",
        ]
        if zaznam:
            radky.append("  - zruseno")
        radky += [
            "---",
            "",
        ]
        if zaznam:
            kdo = f", a to předpisem {zaznam['cim']}" if zaznam.get("cim") else ""
            radky += [
                "> [!danger] Zrušený předpis",
                f"> {predpis} Sb. byl zrušen k {zaznam['k']}{kdo}" + ("" if kdo else ".") + " Rozhodnutí níž vykládají",
                "> právo, které se dnes nepoužije — mají cenu jen pro vztahy z doby jeho účinnosti.",
                "",
            ]
        radky += [
            f"# {kotva or f'§ {paragraf}'} předpisu č. {predpis} Sb.",
            "",
            f"Text ustanovení: [[{nazev_predpisu(predpis)}#{kotva}]]" if kotva else
            f"*Ustanovení v platném znění [[{nazev_predpisu(predpis)}|předpisu]] není — "
            "nejspíš ho zrušila pozdější novela. Rozhodnutí níž je přesto vykládají.*",
            "",
            f"Rozhodnutí, která toto ustanovení zmiňují: **{len(rozhodnuti)}**.",
        ]
        if klicova:
            nej = ", ".join(k for k, _ in sorted(klicova.items(), key=lambda kv: -kv[1])[:8])
            radky += ["", f"Nejčastější témata: {nej}."]

        radky += ["", "## Rozhodnutí", "", "| Vydáno | Soud | Značka | ECLI |", "|---|---|---|---|"]
        for r in rozhodnuti[:300]:
            ecli = hodnota(r, "ecli")
            datum = hodnota(r, "datumVydani") or "—"
            radky.append(
                f"| {datum} | {hodnota(r, 'soud')} | {hodnota(r, 'jednaciCislo')} | "
                f"{f'`{ecli}`' if ecli else '—'} |"
            )
        if len(rozhodnuti) > 300:
            radky.append("")
            radky.append(f"*Zobrazeno 300 nejnovějších z {len(rozhodnuti)}; zbytek je v `judikatura/metadata/`.*")

        radky += [
            "",
            "---",
            "",
            "Plný text rozhodnutí: `python3 tools/judikatura.py --text <ECLI>`",
            "",
        ]
        (slozka / f"{paragraf}.md").write_text("\n".join(radky), encoding="utf-8")

    print(f"rejstřík: {sum(1 for _ in JUDIKATURA.rglob('*.md')):,} souborů")


def na_markdown(dokument: dict, meta: dict) -> str:
    """Z odpovědi API udělá čitelné rozhodnutí. API vrací text dvakrát — jednou rozsekaný na
    odstavce se styly, jednou vcelku v `*Text`; bere se to druhé."""
    radky = [
        "---",
        f"ecli: {hodnota(meta, 'ecli')}",
        f"soud: {hodnota(meta, 'soud')}",
        f"znacka: {hodnota(meta, 'jednaciCislo')}",
        f"vydano: {hodnota(meta, 'datumVydani')}",
        "---",
        "",
        f"# {meta.get('soud', 'Rozhodnutí')} — {meta.get('jednaciCislo', '')}".strip(" —"),
        "",
    ]
    for klic, nadpis in (("header", "Záhlaví"), ("verdictText", "Výrok"),
                         ("justificationText", "Odůvodnění"), ("information", "Poučení")):
        obsah = dokument.get(klic)
        if isinstance(obsah, list):  # header a information jsou strukturované
            obsah = "\n\n".join(
                u.get("text", "") for blok in obsah for u in blok.get("texts", []))
        if obsah and str(obsah).strip():
            radky += [f"## {nadpis}", "", str(obsah).strip(), ""]
    return "\n".join(radky)


def stahni_text(ecli: str) -> int:
    """Plný text jednoho rozhodnutí. Do cache, ne do repozitáře — všechny dohromady mají 13 GB."""
    for soubor in sorted(METADATA.glob("*.jsonl.gz")):
        with gzip.open(soubor, "rt", encoding="utf-8") as f:
            for radek in f:
                r = json.loads(radek)
                if r.get("ecli") != ecli:
                    continue
                # ECLI jde do názvu souboru, takže z něj musí zmizet všechno kromě
                # písmen, číslic a tečky — samotná náhrada dvojteček by pustila lomítko.
                jmeno = re.sub(r"[^\w.]+", "_", ecli).strip("_")
                cil = CACHE / "texty" / f"{jmeno}.md"
                cil.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(r["odkaz"], timeout=TIMEOUT) as odpoved:
                    dokument = json.loads(odpoved.read().decode("utf-8"))
                cil.write_text(na_markdown(dokument, r), encoding="utf-8")
                print(f"{cil}  ({cil.stat().st_size / 1024:.0f} kB)")
                return 0
    print(f"ECLI {ecli} není v metadatech — stáhni je nejdřív", file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--od-roku", type=int, default=2020)
    p.add_argument("--do-roku", type=int)
    p.add_argument("--rejstrik", action="store_true", help="jen přestavět rejstřík z už stažených metadat")
    p.add_argument("--min", type=int, default=3, help="kolik rozhodnutí musí na paragraf odkazovat, aby dostal soubor")
    p.add_argument("--text", help="stáhnout plný text podle ECLI")
    args = p.parse_args()

    if args.text:
        return stahni_text(args.text)

    if not args.rejstrik:
        stahni_metadata(args.od_roku, args.do_roku)

    postav_rejstrik(args.min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
