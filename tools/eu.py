"""Stáhne česká znění předpisů EU z EUR-Lexu a uloží je jako markdown do `eu/`.

Nařízení EU platí v Česku přímo a směrnice určují, jak má vypadat český zákon, takže sbírka bez
nich je neúplná. Zdrojem je CELLAR — datový endpoint Úřadu pro publikace EU, ne web EUR-Lexu:
web odpovídá na strojové požadavky kódem 202 a prázdným tělem, kdežto CELLAR vydá dokument rovnou.

    GET https://publications.europa.eu/resource/celex/32016R0679
        Accept: application/xhtml+xml
        Accept-Language: ces

Starší předpisy XHTML nemají a musí se brát jako `text/html`; u některých není ani to, jen PDF,
a ty se přeskočí — extrakce z PDF by si vyžádala knihovnu navíc.

Seznam předpisů dodá SPARQL nad týmž katalogem. Bere se řada REG (nařízení) a DIR (směrnice);
řada DEC jsou z velké části jednotlivé akty typu schválení podpory nebo jmenování a do sbírky
práva nepatří.

    python3 tools/eu.py --seznam          # jen zjistí, co je ke stažení, a uloží do .cache
    python3 tools/eu.py                   # stáhne, co ještě není
    python3 tools/eu.py --celex 32016R0679   # jeden předpis
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
EU = KOREN / "eu"
CACHE = KOREN / ".cache"
SEZNAM = CACHE / "eu-seznam.json"
STAV = CACHE / "eu-stav.json"

CELLAR = "https://publications.europa.eu/resource/celex"
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
TIMEOUT = 180

# Tempo je mírné schválně — je to veřejná služba Úřadu pro publikace, ne náš stroj.
SOUBEH = 3
PAUZA = 0.3

TYPY = {"REG": "nařízení", "DIR": "směrnice"}

# 32016R0679 -> (2016, R, 0679); písmeno určuje řadu (R nařízení, L směrnice, D rozhodnutí)
CELEX_ROZBOR = re.compile(r"^3(\d{4})([A-Z])(\d+)")

tisk = threading.Lock()
zamek = threading.Lock()


def sparql(dotaz: str) -> list[dict]:
    url = f"{SPARQL}?{urllib.parse.urlencode({'query': dotaz, 'format': 'application/sparql-results+json'})}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))["results"]["bindings"]


def stahni_seznam(od_roku: int = 1952, do_roku: int = 2027) -> list[dict]:
    """CELEX všech nařízení a směrnic, které mají české znění.

    Ptá se po jednotlivých letech. Dotaz přes celou řadu naráz endpoint odmítá chybou 500 —
    `ORDER BY` s `OFFSET` nad desítkami tisíc výsledků je pro něj příliš, kdežto jeden rok
    vrátí stovky řádků a projde vždy.
    """
    polozky: dict[str, dict] = {}
    for zkratka, pismeno in (("REG", "R"), ("DIR", "L")):
        print(f"  {TYPY[zkratka]} …", flush=True)
        for rok in range(od_roku, do_roku):
            try:
                radky = sparql(f"""
                    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
                    SELECT DISTINCT ?celex WHERE {{
                      ?work cdm:work_has_resource-type
                            <http://publications.europa.eu/resource/authority/resource-type/{zkratka}> ;
                            cdm:resource_legal_id_celex ?celex .
                      ?expr cdm:expression_belongs_to_work ?work ;
                            cdm:expression_uses_language
                            <http://publications.europa.eu/resource/authority/language/CES> .
                      FILTER(STRSTARTS(STR(?celex), "3{rok}{pismeno}"))
                    }}
                """)
            except Exception as e:  # noqa: BLE001 — jeden rok navíc neshodí celý seznam
                print(f"    {rok}: {type(e).__name__}", flush=True)
                continue

            for r in radky:
                celex = r["celex"]["value"]
                polozky.setdefault(celex, {"celex": celex, "nazev": "", "rada": zkratka})
            if radky:
                print(f"    {rok}: {len(radky):>5,}   (celkem {len(polozky):,})", flush=True)
            time.sleep(0.5)

    SEZNAM.parent.mkdir(parents=True, exist_ok=True)
    SEZNAM.write_text(json.dumps(sorted(polozky.values(), key=lambda x: x["celex"]),
                                 ensure_ascii=False), encoding="utf-8")
    return list(polozky.values())


def stahni_dokument(celex: str) -> tuple[str, str]:
    """Text předpisu a formát, ve kterém přišel. Novější mají XHTML, starší jen HTML."""
    posledni = ""
    for typ in ("application/xhtml+xml", "text/html"):
        pozadavek = urllib.request.Request(
            f"{CELLAR}/{urllib.parse.quote(celex, safe='')}",
            headers={"Accept": typ, "Accept-Language": "ces", "User-Agent": "czech-law-md"},
        )
        try:
            with urllib.request.urlopen(pozadavek, timeout=TIMEOUT) as r:
                obsah = r.read().decode("utf-8", errors="replace")
            # CELLAR vrací chybu i se stavem 200, poznat se dá jen podle délky a obsahu
            if len(obsah) < 1000 or "does not hold a content datastream" in obsah:
                posledni = obsah[:120]
                continue
            # Není-li české znění, vydá CELLAR cizojazyčné. Poznat se dá podle hlavičky
            # Úředního věstníku — česká verze ji má česky.
            if "Avis juridique important" in obsah or (
                    "Official Journal" in obsah and "Úřední věstník" not in obsah):
                posledni = "české znění není, vráceno cizojazyčné"
                continue
            return obsah, typ
        except urllib.error.HTTPError as e:
            posledni = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            posledni = str(e)[:120]
    raise ValueError(posledni or "žádný textový formát")


_ZNACKA = re.compile(r"<[^>]+>")
_SKRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def _text(kus: str) -> str:
    return re.sub(r"[ \t ]+", " ", html.unescape(_ZNACKA.sub(" ", kus))).strip()


def nazev_z_dokumentu(dokument: str) -> str:
    """Název se bere z dokumentu, ne ze SPARQL: `oj-doc-ti` nese titul rozepsaný na několik
    odstavců (druh a číslo, datum, věc), a spojený dá přesně to, čemu se předpis říká."""
    casti = [_text(x) for x in re.findall(r'class="oj-doc-ti"[^>]*>(.*?)</p>', dokument, re.S)]
    casti = [c for c in casti if c and not c.startswith("(Text s významem")]
    if casti:
        return " ".join(casti)
    if (m := re.search(r'class="eli-main-title"[^>]*>(.*?)</div>', dokument, re.S)):
        return _text(m.group(1))

    # Starý formát název nikde neoznačuje: stojí v odstavcích mezi údajem o Úředním věstníku
    # a oslovením orgánu, který předpis přijal („RADA EVROPSKÉHO…"). Bere se tenhle úsek.
    odstavce = [_text(o) for o in re.findall(r"<p[^>]*>(.*?)</p>", dokument, re.S)]
    odstavce = [o for o in odstavce if o and not o.startswith("Důležité právní upozornění")]
    nazev: list[str] = []
    for o in odstavce[:8]:
        if o.startswith("Úřední věstník"):
            continue
        if o.isupper() or o.startswith(("vzhledem", "s ohledem")):
            break
        nazev.append(o)
    return " — ".join(nazev[:2])


def na_markdown(dokument: str, meta: dict) -> str:
    """Z EUR-Lexového XHTML udělá markdown se stejnou stavbou jako české předpisy: každý článek
    je nadpis, takže na něj vede odkaz `[[32016R0679#Článek 6]]` a najde ho i fulltextový index."""
    dokument = _SKRIPT.sub(" ", dokument)
    nazev = nazev_z_dokumentu(dokument) or meta.get("nazev") or meta["celex"]

    rok, pismeno, cislo = "", "", ""
    if (m := CELEX_ROZBOR.match(meta["celex"])):
        rok, pismeno, cislo = m.group(1), m.group(2), m.group(3).lstrip("0")

    radky = [
        "---",
        f"celex: {meta['celex']}",
        f"nazev: {json.dumps(nazev, ensure_ascii=False)}",
        f"druh: {TYPY.get(meta['rada'], meta['rada'])}",
        f"cislo: {rok}/{cislo}" if rok and cislo else "",
        f"rok: {rok}",
        f"eli: https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:{meta['celex']}",
        "tags:",
        "  - eu",
        f"  - {TYPY.get(meta['rada'], 'predpis-eu')}",
        f"  - rok/{rok}" if rok else "",
        "---",
        "",
        f"# {nazev}",
        "",
    ]
    radky = [r for r in radky if r != ""] + [""]

    # Starší předpisy nemají strukturované XHTML, jen holé odstavce; článek se v nich pozná
    # podle toho, že odstavec obsahuje pouze „Článek 5".
    if 'class="eli-subdivision"' not in dokument:
        return _stary_format(dokument, radky)

    # Preambule až po první článek, pak článek po článku.
    casti = re.split(r'<div class="eli-subdivision" id="(art_[^"]+)">', dokument)
    uvod = _text(casti[0])
    if uvod:
        # hlavička Úředního věstníku nahoře je pro čtení k ničemu
        uvod = re.sub(r"^.*?Úřední věstník Evropské unie\s*\S*\s*", "", uvod, flags=re.S)
        radky += [uvod.strip(), ""]

    for i in range(1, len(casti), 2):
        blok = casti[i + 1] if i + 1 < len(casti) else ""
        oznaceni = _text(m.group(1)) if (m := re.search(r'<p[^>]*class="oj-ti-art"[^>]*>(.*?)</p>', blok, re.S)) else casti[i]
        oznaceni = _POCESTI_CLANEK.sub("Článek", oznaceni)
        nadpis = _text(m.group(1)) if (m := re.search(r'<p[^>]*class="oj-sti-art"[^>]*>(.*?)</p>', blok, re.S)) else ""

        telo = re.sub(r'<p[^>]*class="oj-(ti|sti)-art"[^>]*>.*?</p>', " ", blok, flags=re.S)
        syrove = [_text(o) for o in re.findall(r"<p[^>]*>(.*?)</p>", telo, re.S)]
        # EUR-Lex dává písmeno výčtu do vlastního odstavce; samotné „a)" na řádku nic neříká,
        # tak se slepí s textem, který k němu patří, a udělá se z toho položka seznamu.
        odstavce: list[str] = []
        cekajici = ""
        for o in syrove:
            if not o:
                continue
            if re.fullmatch(r"[a-zřšžýáíéúůň]{1,3}\)|[ivxlcIVXLC]{1,5}\)|\d{1,3}\)", o):
                cekajici = o
                continue
            odstavce.append(f"- {cekajici} {o}" if cekajici else o)
            cekajici = ""

        radky.append(f"## {oznaceni}")
        if nadpis:
            radky.append(f"**{nadpis}**")
        radky += odstavce + [""]

    return re.sub(r"\n{3,}", "\n\n", "\n".join(radky)) + "\n"


# CELLAR u části českých dokumentů nechá označení článku anglicky, i když je tělo česky.
# Je to označení struktury, ne text předpisu, takže se srovná — jinak by ta ustanovení
# vypadla z dohledatelnosti jen kvůli jazyku nadpisu.
_POCESTI_CLANEK = re.compile(r"^Article\b", re.I)


# Krátká nařízení, která jen mění jiné, mívají místo „Článek 1“ jediný nečíslovaný „Jediný článek“.
_OZNACENI_CLANKU = re.compile(r"^(?:Jediný\s+článek|(?:Článek|Čl\.|ČLÁNEK)\s+\d+[a-z]*)\.?$", re.I)


def _stary_format(dokument: str, radky: list[str]) -> str:
    """Předpisy vydané před zavedením strukturovaného XHTML. Text je v holých odstavcích,
    takže se článek pozná podle obsahu odstavce, ne podle značky."""
    odstavce = [_text(o) for o in re.findall(r"<p[^>]*>(.*?)</p>", dokument, re.S)]
    odstavce = [o for o in odstavce if o]

    v_clanku = False
    cekajici_nadpis = True
    for o in odstavce:
        if _OZNACENI_CLANKU.match(o):
            radky += ["", f"## {o.rstrip('.')}"]
            v_clanku, cekajici_nadpis = True, True
            continue
        # Krátký odstavec hned za označením článku je jeho nadpis („Základní ustanovení").
        if v_clanku and cekajici_nadpis and len(o) < 80 and not o[0].islower():
            radky.append(f"**{o}**")
            cekajici_nadpis = False
            continue
        cekajici_nadpis = False
        radky.append(o)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(radky)) + "\n"


# CELEX je sektor, rok, druh a číslo, případně s příponou opravy: 32016R0679R(03).
# Cokoli jiného je buď chyba zdroje, nebo pokus zapsat mimo složku — CELLAR je cizí server.
# Přípon může být víc a nemusí mít R: 31966R0017(01), 31999L0031R(02)R(01).
CELEX_PLATNY = re.compile(r"^[1-9]\d{4}[A-Z]{1,2}\d{4}(?:R?\(\d{2}\))*$")


def soubor_pro(celex: str) -> Path:
    if not CELEX_PLATNY.match(celex):
        raise ValueError(f"CELEX {celex!r} nemá platný tvar")
    rok = m.group(1) if (m := CELEX_ROZBOR.match(celex)) else "ostatni"
    return EU / rok / f"{celex}.md"


def zpracuj(polozka: dict, stav: dict) -> str:
    celex = polozka["celex"]
    cil = soubor_pro(celex)
    if celex in stav and cil.exists():
        return "beze-zmeny"

    time.sleep(PAUZA)
    try:
        dokument, format_ = stahni_dokument(celex)
        obsah = na_markdown(dokument, polozka)
        # CELLAR u některých CELEXů vydá jen stylopis bez obsahu; prázdný soubor by v repozitáři
        # vypadal jako stažený předpis, tak se počítá mezi nedostupné.
        if not [r for r in obsah.split("---", 2)[-1].split("\n") if r.strip() and not r.startswith("#")]:
            raise ValueError("dokument bez textu — CELLAR vydal jen obálku")
    except Exception as e:  # noqa: BLE001 — jeden nedostupný předpis nesmí shodit běh
        with tisk:
            print(f"  ! {celex}: {type(e).__name__}: {str(e)[:90]}", flush=True)
        return "chyba"

    cil.parent.mkdir(parents=True, exist_ok=True)
    cil.write_text(obsah, encoding="utf-8")
    with zamek:
        stav[celex] = {"soubor": str(cil.relative_to(KOREN)), "format": format_}
    return "novy"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seznam", action="store_true", help="jen zjistit, co je ke stažení")
    p.add_argument("--celex", help="stáhnout jediný předpis")
    p.add_argument("--limit", type=int, help="zkušební dávka")
    args = p.parse_args()

    if args.celex:
        # jeden předpis se stahuje rovnou, seznam k tomu není potřeba
        polozky = [{"celex": args.celex, "nazev": args.celex, "rada": "REG"}]
        if SEZNAM.exists():
            znamy = [x for x in json.loads(SEZNAM.read_text(encoding="utf-8")) if x["celex"] == args.celex]
            polozky = znamy or polozky
        stav = json.loads(STAV.read_text(encoding="utf-8")) if STAV.exists() else {}
        stav.pop(args.celex, None)
        print(zpracuj(polozky[0], stav))
        STAV.write_text(json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        return 0

    if args.seznam or not SEZNAM.exists():
        polozky = stahni_seznam()
        print(f"seznam: {len(polozky):,} předpisů s českým zněním")
        if args.seznam:
            return 0
    else:
        polozky = json.loads(SEZNAM.read_text(encoding="utf-8"))

    if args.limit:
        polozky = polozky[: args.limit]

    stav = json.loads(STAV.read_text(encoding="utf-8")) if STAV.exists() else {}
    zacatek = time.time()
    pocty = {"novy": 0, "beze-zmeny": 0, "chyba": 0}

    with ThreadPoolExecutor(max_workers=SOUBEH) as bazen:
        for i, vysledek in enumerate(bazen.map(lambda x: zpracuj(x, stav), polozky), start=1):
            pocty[vysledek] += 1
            if i % 100 == 0 or i == len(polozky):
                uplynulo = time.time() - zacatek
                tempo = i / uplynulo if uplynulo else 0
                zbyva = (len(polozky) - i) / tempo / 60 if tempo else 0
                with tisk:
                    print(f"  {i:,}/{len(polozky):,}  nové {pocty['novy']}  "
                          f"beze změny {pocty['beze-zmeny']}  chyby {pocty['chyba']}  "
                          f"{tempo:.1f}/s  zbývá ~{zbyva:.0f} min", flush=True)
                STAV.write_text(json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    STAV.write_text(json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"\nhotovo za {(time.time() - zacatek) / 60:.0f} min: "
          f"nové {pocty['novy']:,}, beze změny {pocty['beze-zmeny']:,}, chyby {pocty['chyba']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
