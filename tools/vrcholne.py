"""Judikatura vrcholných soudů — Nejvyšší soud strojově, Ústavní a Nejvyšší správní odkazem.

Otevřená data justice.cz končí u vrchních soudů, takže rozhodnutí Nejvyššího, Ústavního
a Nejvyššího správního soudu v `judikatura/` nejsou. Každý z těch tří soudů má vlastní databázi
a každá je jinak přístupná:

* **Nejvyšší soud** běží na Lotus Domino a prohledá se prostým GETem, takže se dá číst strojově.
  To dělá tenhle nástroj.
* **Ústavní soud** (NALUS) je ASP.NET WebForms: bez POSTu s `__VIEWSTATE` a session cookie
  nevydá ani seznam. Text jednotlivého rozhodnutí sice na přímé URL je, ale její parametr je
  interní identifikátor, který se ze spisové značky odvodit nedá.
* **Nejvyšší správní soud** má vyhledávač v ASP.NET Core s CSRF tokenem a povinnými poli, která
  se bez reverzního inženýrství formuláře netrefí.

U obou zbylých soudů proto nástroj vrací návod, kam jít a co tam zadat — což je pro agenta
užitečnější než mlčení.

    python3 tools/vrcholne.py --hledej "nájem bytu"       # rozhodnutí Nejvyššího soudu
    python3 tools/vrcholne.py --paragraf 2235 --predpis 89/2012
    python3 tools/vrcholne.py --text "29 Cdo 937/99"      # plný text do .cache
    python3 tools/vrcholne.py --kam "nájem bytu"          # kde hledat u ÚS a NSS
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
CACHE = KOREN / ".cache" / "vrcholne"

NS = "https://rozhodnuti.nsoud.cz"
NS_HLEDANI = f"{NS}/judikatura/judikatura_ns.nsf/WebSearch?SearchView&SearchMax=0&Query="
TIMEOUT = 90

# Značka je text odkazu: 29 Cdo 937/99, 29 NSCR 83/2014, Cpjn 200/2013. Výčet rejstříků by
# tiše zahodil každý, na který se zapomnělo — insolvenční NSCR byly dvě třetiny výsledků.
ODKAZ = re.compile(r'href="(/judikatura/judikatura_ns\.nsf/[0-9a-f]{32}/[0-9a-f]{32}\?OpenDocument[^"]*)"'
                   r'[^>]*>\s*([^<]{3,40}?)\s*</a>')
# Insolvenční věci nemají „Spisovou“, ale „Senátní značku“.
ZNACKA_STR = re.compile(r"(?:Spisová|Senátní) značka\s*:?\s*([^\n<]{3,40})")
ECLI_STR = re.compile(r"ECLI:\s*(ECLI:CZ:NS:[0-9A-Z.:]+)")
DATUM = re.compile(r"Datum rozhodnutí\s*:?\s*([\d\s.]{6,20})")
# Kde stránka ECLI nevypíše, sestaví se ze značky a roku deterministicky: 26 Cdo 761/2021 + 2021 -> ECLI:CZ:NS:2021:26.CDO.761.2021.1
ROZBOR_ZNACKY = re.compile(r"(\d+)\s+([A-Za-z]+)\s+(\d+)/(\d{2,4})")

_TAG = re.compile(r"<[^>]+>")
_SKRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def _stahni(url: str) -> str:
    pozadavek = urllib.request.Request(url, headers={"User-Agent": "czech-law-md"})
    with urllib.request.urlopen(pozadavek, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


def _text(kus: str) -> str:
    kus = _SKRIPT.sub(" ", kus)
    return re.sub(r"[ \t ]+", " ", html.unescape(_TAG.sub(" ", kus))).strip()


def hledej(dotaz: str, limit: int = 30) -> list[dict]:
    """Rozhodnutí Nejvyššího soudu k dotazu. Vrací značku a odkaz na plný text.

    Znak § se indexuje jen ve frázi: `§ 2235` nevrátí nic, `"§ 2235"` vrátí 43 rozhodnutí."""
    stranka = _stahni(NS_HLEDANI + urllib.parse.quote(dotaz))

    nalezy: dict[str, dict] = {}
    for m in ODKAZ.finditer(stranka):
        znacka = re.sub(r"\s+", " ", html.unescape(m.group(2)))
        nalezy.setdefault(znacka, {"znacka": znacka, "odkaz": NS + html.unescape(m.group(1))})
        if len(nalezy) >= limit:
            break
    return list(nalezy.values())


def rozhodnuti(odkaz_nebo_znacka: str) -> dict:
    """Plný text jednoho rozhodnutí Nejvyššího soudu."""
    odkaz = odkaz_nebo_znacka
    if not odkaz.startswith("http"):
        # Značka se hledá ve frázi, jinak Domino rozloží „26 Cdo 1616/98“ na slova a vrátí
        # jiná rozhodnutí téhož senátu. Nepřesná shoda se nikdy nebere jako náhrada: agent by
        # citoval rozhodnutí, o které nežádal, a nepoznal by to.
        znacka = odkaz_nebo_znacka.strip().strip('"')
        nalezy = hledej(f'"{znacka}"', limit=10)
        shoda = [n for n in nalezy if stejna_znacka(n["znacka"], znacka)]
        if not shoda:
            blizke = ", ".join(n["znacka"] for n in nalezy[:3]) or "nic"
            raise ValueError(f"rozhodnutí {znacka} nenalezeno (nejblíž: {blizke})")
        odkaz = shoda[0]["odkaz"]

    stranka = _stahni(odkaz)
    cisty = _text(stranka)
    znacka = m.group(1).strip() if (m := ZNACKA_STR.search(cisty)) else ""
    datum = m.group(1).strip() if (m := DATUM.search(cisty)) else ""
    return {
        "znacka": znacka,
        "datum": datum,
        "ecli": m.group(1) if (m := ECLI_STR.search(cisty)) else sestav_ecli(znacka, datum),
        "odkaz": odkaz,
        "text": cisty,
    }


def stejna_znacka(a: str, b: str) -> bool:
    """Databáze píše „NSCR“, agent spíš „NSČR“; mezery a velikost písmen taky nerozhodují."""
    def klic(z: str) -> str:
        bez = unicodedata.normalize("NFKD", z).encode("ascii", "ignore").decode()
        return re.sub(r"\s+", "", bez).casefold()
    return klic(a) == klic(b)


def sestav_ecli(znacka: str, datum: str) -> str:
    """ECLI Nejvyššího soudu ze spisové značky a data rozhodnutí."""
    m = ROZBOR_ZNACKY.search(znacka)
    rok = re.search(r"(\d{4})\s*$", datum.strip())
    if not m or not rok:
        return ""
    cislo_roku = m.group(4)
    if len(cislo_roku) == 2:  # staré značky mají rok dvojmístně: 937/99
        cislo_roku = ("19" if cislo_roku > "50" else "20") + cislo_roku
    return f"ECLI:CZ:NS:{rok.group(1)}:{m.group(1)}.{m.group(2).upper()}.{m.group(3)}.{cislo_roku}.1"


def na_markdown(r: dict) -> str:
    radky = [
        "---",
        f"soud: Nejvyšší soud",
        f"znacka: {r['znacka']}",
        f"datum: {r['datum']}",
        f"ecli: {r['ecli']}",
        "tags:",
        "  - judikatura",
        "  - nejvyssi-soud",
        "---",
        "",
        f"# Nejvyšší soud — {r['znacka'] or 'rozhodnutí'}",
        "",
        f"Zdroj: {r['odkaz']}",
        "",
        r["text"],
        "",
    ]
    return "\n".join(radky)


# Co umí druhé dva soudy a jak se k nim dostat ručně.
KAM = {
    "Ústavní soud": {
        "kde": "https://nalus.usoud.cz",
        "proc": "ASP.NET WebForms — seznam vydá jen na POST s __VIEWSTATE a session cookie",
        "jak": "Ve formuláři na nalus.usoud.cz/Search/Search.aspx zaškrtni Nálezy i Usnesení "
               "a hledej v odůvodnění. Spisová značka má tvar „I. ÚS 1234/20“, plénum „Pl. ÚS 24/10“.",
        "navic": "Vybraná rozhodnutí vycházejí ve Sbírce nálezů a usnesení: "
                 "https://www.usoud.cz/sbirka-nalezu-a-usneseni-us",
    },
    "Nejvyšší správní soud": {
        "kde": "https://vyhledavac.nssoud.cz",
        "proc": "ASP.NET Core s CSRF tokenem a povinnými poli formuláře",
        "jak": "Ve vyhledávači zadej text nebo spisovou značku; ta má tvar „7 Afs 123/2019“ "
               "(Afs daně, As správní, Ads sociální, Azs azyl).",
        "navic": "Zásadní rozhodnutí jsou ve Sbírce NSS: https://sbirka.nssoud.cz",
    },
}


def kam_jit(dotaz: str = "") -> str:
    casti = ["# Kde hledat judikaturu vrcholných soudů", ""]
    casti.append("**Nejvyšší soud** prohledá `tools/vrcholne.py --hledej` rovnou z příkazové řádky.")
    if dotaz:
        casti.append(f"Zkus: `python3 tools/vrcholne.py --hledej \"{dotaz}\"`")
    casti.append("")
    for soud, info in KAM.items():
        casti += [
            f"## {soud}",
            "",
            f"Strojově to nejde: {info['proc']}.",
            "",
            f"- databáze: {info['kde']}",
            f"- {info['jak']}",
            f"- {info['navic']}",
            "",
        ]
    casti.append("Rozcestník Ministerstva spravedlnosti: "
                 "https://msp.gov.cz/web/msp/rozhodnuti-soudu-judikatura-")
    return "\n".join(casti)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hledej", help="dotaz do databáze Nejvyššího soudu")
    p.add_argument("--paragraf", help="číslo ustanovení, např. 2235")
    p.add_argument("--predpis", help="k --paragraf, např. 89/2012")
    p.add_argument("--text", help="plný text podle spisové značky nebo odkazu")
    p.add_argument("--kam", nargs="?", const="", help="kde hledat u Ústavního a Nejvyššího správního soudu")
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args()

    if args.kam is not None:
        print(kam_jit(args.kam))
        return 0

    if args.text:
        try:
            r = rozhodnuti(args.text)
        except ValueError as e:
            print(f"  {e}")
            return 1
        CACHE.mkdir(parents=True, exist_ok=True)
        jmeno = re.sub(r"[^\w]+", "-", r["znacka"] or "rozhodnuti").strip("-")
        cil = CACHE / f"{jmeno}.md"
        cil.write_text(na_markdown(r), encoding="utf-8")
        print(f"{cil}  ({cil.stat().st_size / 1024:.0f} kB)")
        return 0

    dotaz = args.hledej
    if args.paragraf:
        # Znak § se indexuje, ale jen ve frázi: `§ 2235` bez uvozovek nevrátí nic, `"§ 2235"`
        # vrátí 43 rozhodnutí. Bez uvozovek by navíc číslo trefilo i spisové značky.
        par = args.paragraf.lstrip("§ ").strip()
        dotaz = f'"§ {par}"'
        if args.predpis:
            nazev = {"89/2012": "občanského zákoníku", "262/2006": "zákoníku práce",
                     "99/1963": "občanského soudního řádu", "40/2009": "trestního zákoníku",
                     "500/2004": "správního řádu", "141/1961": "trestního řádu"}
            if (slovy := nazev.get(args.predpis)):
                dotaz += f" AND {slovy}"

    if not dotaz:
        p.print_help()
        return 1

    nalezy = hledej(dotaz, args.limit)
    print(f"Nejvyšší soud — „{dotaz}“: {len(nalezy)} rozhodnutí\n")
    for n in nalezy:
        print(f"  {n['znacka']}")
    if nalezy:
        print(f"\nPlný text: python3 tools/vrcholne.py --text \"{nalezy[0]['znacka']}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
