"""Stáhne česká znění předpisů EU z EUR-Lexu a uloží je jako markdown do `eu/`.

Nařízení EU platí v Česku přímo a směrnice určují, jak má vypadat český zákon, takže sbírka bez
nich je neúplná. Zdrojem je CELLAR — datový endpoint Úřadu pro publikace EU, ne web EUR-Lexu:
web odpovídá na strojové požadavky kódem 202 a prázdným tělem, kdežto CELLAR vydá dokument rovnou.

    GET https://publications.europa.eu/resource/celex/32016R0679
        Accept: application/xhtml+xml
        Accept-Language: ces

Starší předpisy XHTML nemají a musí se brát jako `text/html`; u některých není ani to, jen PDF,
a ty se přeskočí — extrakce z PDF by si vyžádala knihovnu navíc.

Seznam předpisů dodá SPARQL nad týmž katalogem. Berou se nařízení a směrnice včetně prováděcích
a v přenesené pravomoci — CELLAR je od roku 2013 vede jako samostatné typy (REG_IMPL, REG_DEL,
DIR_IMPL, DIR_DEL) a bez nich chybělo skoro 15 tisíc předpisů, mezi nimi regulační technické
normy k PSD2. Řada DEC jsou z velké části jednotlivé akty typu schválení podpory nebo jmenování
a do sbírky práva nepatří. Každý běh obnoví seznam za letošní a loňský rok — dřív se stáhl jednou a nové
předpisy už nepřibývaly.

**Text článků je konsolidované znění platné dnes**, ne původní znění z Úředního věstníku.
Původní znění PSD2 neobsahuje novely z let 2022 a 2024; konsolidace k 17. 1. 2025 ano.
Konsolidace se jmenuje CELEXem s nulou místo sektoru a datem, od kterého platí:
`02015L2366-20250117`. Odůvodnění v novějších konsolidacích není, a protože ho novely nemění,
bere se z původního znění. Zrušený předpis dostane poslední znění před zrušením: konsolidace
ke dni zrušení bývá prázdná obálka s jediným slovem „zrušeno“. Konsolidace je informativní,
závazné zůstává znění v Úředním věstníku.

    python3 tools/eu.py --seznam          # jen zjistí, co je ke stažení, a uloží do .cache
    python3 tools/eu.py                   # stáhne, co ještě není nebo má nové znění
    python3 tools/eu.py --celex 32016R0679   # jeden předpis
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
EU = KOREN / "eu"
CACHE = KOREN / ".cache"
SEZNAM = CACHE / "eu-seznam.json"
STAV = CACHE / "eu-stav.json"
ZRUSENE = KOREN / ".zmeny" / "platnost-eu.json"

CELLAR = "https://publications.europa.eu/resource/celex"
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
TIMEOUT = 180

# Tempo je mírné schválně — je to veřejná služba Úřadu pro publikace, ne náš stroj.
SOUBEH = 3
PAUZA = 0.3

# Typ v CELLARu -> písmeno řady v CELEXu, druh do frontmatteru a tag. Tag zůstává obecný,
# aby pohledy v Obsidianu nemusely znát každý podtyp.
TYPY = {
    "REG": ("R", "nařízení", "nařízení"),
    "REG_IMPL": ("R", "prováděcí nařízení", "nařízení"),
    "REG_DEL": ("R", "nařízení v přenesené pravomoci", "nařízení"),
    "DIR": ("L", "směrnice", "směrnice"),
    "DIR_IMPL": ("L", "prováděcí směrnice", "směrnice"),
    "DIR_DEL": ("L", "směrnice v přenesené pravomoci", "směrnice"),
}


def druh_aktu(rada: str) -> str:
    return TYPY[rada][1] if rada in TYPY else rada


def tag_aktu(rada: str) -> str:
    return TYPY[rada][2] if rada in TYPY else "predpis-eu"

# 32016R0679 -> (2016, R, 0679); písmeno určuje řadu (R nařízení, L směrnice, D rozhodnutí)
CELEX_ROZBOR = re.compile(r"^3(\d{4})([A-Z])(\d+)")

tisk = threading.Lock()
zamek = threading.Lock()


def sparql(dotaz: str) -> list[dict]:
    url = f"{SPARQL}?{urllib.parse.urlencode({'query': dotaz, 'format': 'application/sparql-results+json'})}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))["results"]["bindings"]


def stahni_seznam(od_roku: int = 1952, do_roku: int | None = None) -> list[dict]:
    """CELEX všech nařízení a směrnic, které mají české znění, za roky od–do včetně.

    Ptá se po jednotlivých letech. Dotaz přes celou řadu naráz endpoint odmítá chybou 500 —
    `ORDER BY` s `OFFSET` nad desítkami tisíc výsledků je pro něj příliš, kdežto jeden rok
    vrátí stovky řádků a projde vždy.
    """
    do_roku = do_roku or datetime.date.today().year
    polozky: dict[str, dict] = {}
    for zkratka, (pismeno, nazev_druhu, _) in TYPY.items():
        print(f"  {nazev_druhu} …", flush=True)
        # Prováděcí a delegované předpisy vede CELLAR zvlášť až od roku 2013 (první je 32013R0246).
        for rok in range(od_roku if zkratka in ("REG", "DIR") else max(od_roku, 2010), do_roku + 1):
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
    return list(polozky.values())


def uloz_seznam(polozky: list[dict]) -> None:
    SEZNAM.parent.mkdir(parents=True, exist_ok=True)
    SEZNAM.write_text(json.dumps(sorted(polozky, key=lambda x: x["celex"]), ensure_ascii=False),
                      encoding="utf-8")


# 02015L2366-20250117 -> základ 32015L2366, znění od 2025-01-17
KONSOLIDACE_CELEX = re.compile(r"^0(\d{4}[A-Z]{1,2}\d{4}(?:\(\d{2}\))?)-(\d{4})(\d{2})(\d{2})$")


def stahni_konsolidace() -> dict[str, list[str]]:
    """Ke každému předpisu data jeho konsolidovaných znění, která existují česky, vzestupně.
    Bez filtru na jazyk by se bralo i znění, které česky není — starší konsolidace vznikaly
    před rokem 2004 a nové bývají přeložené s odstupem."""
    radky = sparql("""
        PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
        SELECT ?celex WHERE {
          ?work cdm:resource_legal_id_celex ?celex .
          FILTER(STRSTARTS(STR(?celex), "0"))
          ?expr cdm:expression_belongs_to_work ?work ;
                cdm:expression_uses_language
                <http://publications.europa.eu/resource/authority/language/CES> .
        }
    """)
    podle: dict[str, set[str]] = defaultdict(set)
    for r in radky:
        if m := KONSOLIDACE_CELEX.match(r["celex"]["value"]):
            podle["3" + m.group(1)].add(f"{m.group(2)}-{m.group(3)}-{m.group(4)}")
    return {celex: sorted(data) for celex, data in podle.items()}


def celex_zneni(celex: str, od: str) -> str:
    return f"0{celex[1:]}-{od.replace('-', '')}"


def vyber_zneni(data: list[str], dnes: str, zruseno_k: str = "",
                prazdne: tuple[str, ...] | list[str] = ()) -> tuple[list[str], str]:
    """Kandidáti na dnes platné znění od nejnovějšího a nejbližší budoucí znění.

    U zrušeného předpisu jen znění do konce platnosti: konsolidace ke dni zrušení bývá prázdná
    obálka „zrušeno“. Znění, které se už jednou ukázalo prázdné, se znovu nezkouší."""
    hranice = min(dnes, zruseno_k) if zruseno_k else dnes
    kandidati = [d for d in reversed(data) if d <= hranice and d not in prazdne]
    pristi = "" if zruseno_k else next((d for d in data if d > dnes), "")
    return kandidati, pristi


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
        f"druh: {druh_aktu(meta['rada'])}",
        f"cislo: {rok}/{cislo}" if rok and cislo else "",
        f"rok: {rok}",
        f"eli: https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:{meta['celex']}",
        "tags:",
        "  - eu",
        f"  - {tag_aktu(meta['rada'])}",
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


# Značky konsolidace: ▼B (původní text), ►M1 … ◄ (novela), ►C1 (oprava). Do textu nepatří.
_ZNACKY_NOVEL = re.compile(r"[►▼][A-Z]{0,2}\d*|◄")
# Odstavce, které nesou jen značku novely nebo hlavičku dokumentu, ne text předpisu.
_PRESKOCIT = {"modref", "arrow", "reference", "disclaimer", "hd-modifiers", "separator"}
_BLOKOVE = {"p", "div", "td", "th", "tr", "table", "li", "ul", "ol", "dl", "dt", "dd",
            "h1", "h2", "h3", "h4", "h5", "h6", "hr", "br", "body"}


class _Zneni(HTMLParser):
    """Konsolidované znění jako sled bloků (druh, text).

    Konsolidace má jinou stavbu než Úřední věstník: číslo odstavce („1. “) a písmeno výčtu
    („a) “) stojí ve vlastním `span`/`div` mimo odstavec, takže čtení po `<p>` by je ztratilo."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bloky: list[tuple[str, str]] = []
        self.zasobnik: list[tuple[str, str]] = []
        self.text: list[str] = []
        self.predpona: list[str] = []
        self.druh_predpony = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tridy = set((dict(attrs).get("class") or "").split())
        if tag in ("script", "style") or tridy & _PRESKOCIT:
            role = "preskocit"
        elif "no-parag" in tridy:
            role = "cislo"
        elif "grid-list-column-1" in tridy:
            role = "pismeno"
        elif "title-article-norm" in tridy:
            role = "clanek"
        elif "stitle-article-norm" in tridy:
            role = "nadpis"
        elif "title-annex-1" in tridy:
            role = "priloha"
        elif tag in _BLOKOVE:
            role = "odstavec" if tag == "p" else "blok"
        else:
            role = "inline"
        if role not in ("inline", "cislo", "pismeno", "preskocit"):
            self._vypust("odstavec")
        if role in ("cislo", "pismeno"):
            self.predpona, self.druh_predpony = [], role
        self.zasobnik.append((tag, role))

    def handle_endtag(self, tag: str) -> None:
        if not any(t == tag for t, _ in self.zasobnik):
            return
        while self.zasobnik:
            t, role = self.zasobnik.pop()
            if t == tag:
                break
        if role in ("clanek", "nadpis", "priloha", "odstavec", "blok"):
            self._vypust(role if role in ("clanek", "nadpis", "priloha") else "odstavec")

    def handle_data(self, data: str) -> None:
        role = {r for _, r in self.zasobnik}
        if "preskocit" in role:
            return
        (self.predpona if role & {"cislo", "pismeno"} else self.text).append(data)

    def _vypust(self, druh: str) -> None:
        text = _cisty("".join(self.text))
        self.text = []
        if not text:
            return
        predpona = _cisty("".join(self.predpona))
        if druh == "odstavec" and predpona:
            text = f"- {predpona} {text}" if self.druh_predpony == "pismeno" else f"{predpona} {text}"
            self.predpona = []
        self.bloky.append((druh, text))


def _cisty(text: str) -> str:
    # Konec novely před interpunkcí („… jiné ◄;“) by po náhradě mezerou nechal „jiné ;“.
    text = re.sub(r"\s*◄\s*(?=[,.;:)])", "", text)
    return re.sub(r"\s+", " ", _ZNACKY_NOVEL.sub(" ", text)).strip()


def preambule(md: str) -> list[str]:
    """Odůvodnění z dosavadního souboru: řádky mezi nadpisem předpisu a prvním článkem.
    Předpis bez článků nese celý obsah v odůvodnění a dal by se tak dvakrát — pak nic."""
    radky = md.split("\n")
    zacatek = next((i for i, r in enumerate(radky) if r.startswith("# ")), None)
    konec = next((i for i, r in enumerate(radky) if r.startswith("## ")), None)
    if zacatek is None or konec is None or konec < zacatek:
        return []
    vyber = radky[zacatek + 1:konec]
    while vyber and not vyber[-1].strip():
        vyber.pop()
    while vyber and not vyber[0].strip():
        vyber.pop(0)
    return vyber


def novely(dokument: str) -> list[str]:
    """CELEXy novel z tabulky „Ve znění“ v hlavičce konsolidace, v pořadí, jak jdou."""
    i = dokument.find('class="hd-modifiers"')
    if i == -1:
        return []
    # Jen tabulka „Ve znění“: dál v dokumentu jsou odkazy na předpisy, které nic neměnily.
    konec = dokument.find("</table>", i)
    opravy = dokument.find("Opravena", i)
    blok = dokument[i:opravy if -1 < opravy < konec else konec]
    vysledek: list[str] = []
    for celex in re.findall(r'title="(3\d{4}[A-Z]{1,2}\d{4}(?:\(\d{2}\))?)', blok):
        if celex not in vysledek:
            vysledek.append(celex)
    return vysledek


def zneni_na_markdown(dokument: str, meta: dict, uvod: list[str], od: str, pristi: str,
                      odkaz=str) -> str:
    """Složí předpis z konsolidovaného znění: hlavička, odůvodnění z původního znění, články
    a přílohy z konsolidace. Vrátí prázdno, když konsolidace žádný článek nemá — to je
    obálka zrušeného předpisu, ne text."""
    cteni = _Zneni()
    cteni.feed(_SKRIPT.sub(" ", dokument))
    cteni.close()
    prvni = next((i for i, (druh, _) in enumerate(cteni.bloky) if druh == "clanek"), None)
    if prvni is None:
        return ""

    # Titul stojí v hlavičce i nad textem; bere se jen z hlavičky, ať se nezdvojí.
    konec_hlavicky = next((i for i in (dokument.find('class="hd-modifiers"'),
                                       dokument.find('class="title-article-norm"')) if i != -1), len(dokument))
    hlavicka = [_cisty(_text(x)) for x in re.findall(
        r'<p class="title-doc-first"[^>]*>(.*?)</p>', dokument[:konec_hlavicky], re.S)]
    nazev = " ".join(h for h in hlavicka if h) or meta.get("nazev") or meta["celex"]
    rok, cislo = "", ""
    if (m := CELEX_ROZBOR.match(meta["celex"])):
        rok, cislo = m.group(1), m.group(3).lstrip("0")
    zneni = celex_zneni(meta["celex"], od)
    url = f"https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:{zneni}"

    radky = [
        "---",
        f"celex: {meta['celex']}",
        f"nazev: {json.dumps(nazev, ensure_ascii=False)}",
        f"druh: {druh_aktu(meta['rada'])}",
        f"cislo: {rok}/{cislo}" if rok and cislo else "",
        f"rok: {rok}",
        f"eli: https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:{meta['celex']}",
        f"zneni: {zneni}",
        f"ucinnost_od: {od}",
        f"pristi_zneni_od: {pristi}" if pristi else "",
        "tags:",
        "  - eu",
        f"  - {tag_aktu(meta['rada'])}",
        f"  - rok/{rok}" if rok else "",
        "---",
    ]
    radky = [r for r in radky if r != ""]
    ve_zneni = ", ".join(odkaz(c) for c in novely(dokument))
    radky += [
        f"> [!info] Konsolidované znění od {od}",
        f"> Články jsou ve znění platném od {od}" + (f", tedy včetně novel {ve_zneni}" if ve_zneni else "")
        + ". Konsolidace je informativní, závazné je znění v Úředním věstníku"
        + ("; odůvodnění je z původního znění" if uvod else "") + f". EUR-Lex: {url}",
        "",
    ]
    if pristi:
        radky += [
            "> [!warning] Chystá se nové znění",
            f"> Tady je znění platné dnes. Od {pristi} platí nové, už vyhlášené: "
            f"https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:{celex_zneni(meta['celex'], pristi)}",
            "",
        ]
    radky += [f"# {nazev}", ""] + uvod + [""]

    predchozi = ""
    for druh, text in cteni.bloky[prvni:]:
        if druh == "clanek":
            radky += ["", f"## {_POCESTI_CLANEK.sub('Článek', text)}"]
        elif druh == "nadpis" and predchozi == "clanek":
            radky.append(f"**{text}**")
        elif druh == "priloha":
            radky += ["", f"## {text}"]
        else:
            radky.append(text)
        predchozi = druh
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


def puvodni_zneni(polozka: dict) -> tuple[str, str]:
    dokument, format_ = stahni_dokument(polozka["celex"])
    obsah = na_markdown(dokument, polozka)
    # CELLAR u některých CELEXů vydá jen stylopis bez obsahu; prázdný soubor by v repozitáři
    # vypadal jako stažený předpis, tak se počítá mezi nedostupné.
    if not [r for r in obsah.split("---", 2)[-1].split("\n") if r.strip() and not r.startswith("#")]:
        raise ValueError("dokument bez textu — CELLAR vydal jen obálku")
    return obsah, format_


def odkaz_na(celex: str) -> str:
    try:
        return f"[[{celex}]]" if soubor_pro(celex).exists() else celex
    except ValueError:
        return celex


def zpracuj(polozka: dict, stav: dict, konsolidace: dict[str, list[str]],
            zrusene: dict[str, str], dnes: str) -> str:
    celex = polozka["celex"]
    cil = soubor_pro(celex)
    znamy = stav.get(celex, {})
    kandidati, pristi = vyber_zneni(konsolidace.get(celex, []), dnes, zrusene.get(celex, ""),
                                    znamy.get("prazdne", ()))
    # Předpis bez konsolidace zůstává v původním znění a stav u něj „zneni“ nemá.
    if (cil.exists() and celex in stav and znamy.get("zneni", "") == (kandidati[0] if kandidati else "")
            and znamy.get("pristi", "") == pristi):
        return "beze-zmeny"

    time.sleep(PAUZA)
    prazdne = list(znamy.get("prazdne", ()))
    try:
        if cil.exists():
            puvodni, format_ = cil.read_text(encoding="utf-8"), znamy.get("format", "")
        else:
            try:
                puvodni, format_ = puvodni_zneni(polozka)
            except ValueError:
                # Část starých předpisů česky v Úředním věstníku není, ale konsolidace ano.
                if not kandidati:
                    raise
                puvodni, format_ = "", ""
        obsah, zneni = "", ""
        for od in kandidati[:3]:
            try:
                dokument, format_zneni = stahni_dokument(celex_zneni(celex, od))
            except ValueError:
                continue
            obsah = zneni_na_markdown(dokument, polozka, preambule(puvodni), od, pristi, odkaz_na)
            if obsah:
                zneni, format_ = od, format_zneni
                break
            prazdne.append(od)
        if not obsah:
            if not puvodni:
                raise ValueError("není ani původní, ani konsolidované znění")
            # Žádná použitelná konsolidace: zpátky na původní znění, i kdyby v souboru už
            # byla starší konsolidace — jinak by tam zůstal text, který neplatí.
            obsah, format_ = (puvodni, format_) if not znamy.get("zneni") else puvodni_zneni(polozka)
    except Exception as e:  # noqa: BLE001 — jeden nedostupný předpis nesmí shodit běh
        with tisk:
            print(f"  ! {celex}: {type(e).__name__}: {str(e)[:90]}", flush=True)
        return "chyba"

    cil.parent.mkdir(parents=True, exist_ok=True)
    zmena = not cil.exists() or cil.read_text(encoding="utf-8") != obsah
    if zmena:
        cil.write_text(obsah, encoding="utf-8")
    zaznam = {"soubor": str(cil.relative_to(KOREN)), "format": format_}
    zaznam.update({k: v for k, v in (("zneni", zneni), ("pristi", pristi if zneni else ""),
                                     ("prazdne", sorted(set(prazdne)))) if v})
    with zamek:
        stav[celex] = zaznam
    return ("zneni" if zneni else "novy") if zmena else "beze-zmeny"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seznam", action="store_true", help="jen zjistit, co je ke stažení")
    p.add_argument("--celex", help="stáhnout jediný předpis")
    p.add_argument("--limit", type=int, help="zkušební dávka")
    args = p.parse_args()

    dnes = datetime.date.today().isoformat()
    zrusene = json.loads(ZRUSENE.read_text(encoding="utf-8")) if ZRUSENE.exists() else {}
    stav = json.loads(STAV.read_text(encoding="utf-8")) if STAV.exists() else {}
    seznam = json.loads(SEZNAM.read_text(encoding="utf-8")) if SEZNAM.exists() else []

    if args.celex:
        # jeden předpis se stahuje rovnou, seznam k tomu není potřeba
        polozky = [x for x in seznam if x["celex"] == args.celex] or \
            [{"celex": args.celex, "nazev": args.celex, "rada": "REG"}]
        stav.pop(args.celex, None)
        print(zpracuj(polozky[0], stav, stahni_konsolidace(), zrusene, dnes))
        STAV.write_text(json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        return 0

    # Celý seznam jen poprvé nebo na požádání; jinak stačí letošní a loňský rok, starší
    # předpisy už nepřibývají.
    rok = datetime.date.today().year
    nove = stahni_seznam() if args.seznam or not seznam else stahni_seznam(od_roku=rok - 1)
    polozky = list({x["celex"]: x for x in seznam + nove}.values())
    uloz_seznam(polozky)
    print(f"seznam: {len(polozky):,} předpisů s českým zněním")
    if args.seznam:
        return 0

    print("  konsolidovaná znění …", flush=True)
    konsolidace = stahni_konsolidace()
    print(f"  předpisů s českou konsolidací: {len(konsolidace):,}")

    if args.limit:
        polozky = polozky[: args.limit]

    zacatek = time.time()
    pocty = {"novy": 0, "zneni": 0, "beze-zmeny": 0, "chyba": 0}

    with ThreadPoolExecutor(max_workers=SOUBEH) as bazen:
        for i, vysledek in enumerate(bazen.map(lambda x: zpracuj(x, stav, konsolidace, zrusene, dnes), polozky),
                                     start=1):
            pocty[vysledek] += 1
            if i % 500 == 0 or i == len(polozky):
                uplynulo = time.time() - zacatek
                tempo = i / uplynulo if uplynulo else 0
                zbyva = (len(polozky) - i) / tempo / 60 if tempo else 0
                with tisk:
                    print(f"  {i:,}/{len(polozky):,}  nové {pocty['novy']}  nové znění {pocty['zneni']}  "
                          f"beze změny {pocty['beze-zmeny']}  chyby {pocty['chyba']}  "
                          f"{tempo:.1f}/s  zbývá ~{zbyva:.0f} min", flush=True)
                STAV.write_text(json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    STAV.write_text(json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"\nhotovo za {(time.time() - zacatek) / 60:.0f} min: nové {pocty['novy']:,}, "
          f"nové znění {pocty['zneni']:,}, beze změny {pocty['beze-zmeny']:,}, chyby {pocty['chyba']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
