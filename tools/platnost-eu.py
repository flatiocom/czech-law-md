"""Doplní k předpisům EU údaj o konci platnosti.

`platnost.py` bere zrušení z konsolidačních vazeb e-Sbírky, které o právu EU nic nevědí, takže
všech 24 026 nařízení a směrnic se ve výsledcích tvářilo jako živé právo — i směrnice 95/46/ES,
kterou v roce 2018 nahradilo GDPR.

CELLAR vydá potřebné údaje dvěma SPARQL dotazy místo 24 tisíc stažení: příznak
`resource_legal_in-force` (podle něj píše EUR-Lex „In force“) a data konce platnosti
`resource_legal_date_end-of-validity`. Hodnota `9999-12-31` znamená „bez konce“.

**Samotné datum konce platnosti nestačí.** Předpis jich mívá víc a část z nich je jen částečný
konec — poznámka `FIN/VAL/PART` nebo `REMPLPART`. PSD2 (32015L2366) má 18. 6. 2026, protože
k tomu dni zrušila směrnice 2023/2673 směrnici 2002/65/ES a s ní čl. 110 PSD2, který ji měnil;
zbytek PSD2 platí. Bez rozlišení se 346 platných předpisů hlásilo jako zrušené a dalších 226
jako zrušené dřív, než k tomu došlo.

Pravidlo:
- **zrušený** je předpis, který CELLAR nevede jako platný, nebo který má v minulosti úplný
  konec platnosti s poznámkou, čím nastal (zrušení, zastarání, konec lhůty) — příznak se po
  uplynutí lhůty někdy nepřepne, 91/672/EHS ho má i po zrušení směrnicí 2017/2397;
- **platí do** se ohlásí u platného předpisu, jehož konec teprve přijde, i když jde o konec
  částečný: 85/374/EHS o vadných výrobcích končí 8. 12. 2026 „částečně“ jen proto, že se dál
  použije na výrobky uvedené na trh dřív.

    python3 tools/platnost-eu.py            # doplní značky do souborů
    python3 tools/platnost-eu.py --nahled   # jen spočítá, nic nezapíše
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Callable

KOREN = Path(__file__).resolve().parent.parent
EU = KOREN / "eu"
PREHLED = KOREN / ".zmeny" / "platnost-eu.json"
POZBUDE_JSON = KOREN / ".zmeny" / "pozbude-eu.json"
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
TIMEOUT = 600

DOTAZ_PLATNY = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?celex ?platny WHERE {
  ?akt cdm:resource_legal_id_celex ?celex ;
       cdm:resource_legal_in-force ?platny .
  FILTER(STRSTARTS(STR(?celex), "3"))
}
"""

# Poznámka visí na trojici přes owl:Axiom; bez ní se částečný konec od úplného nepozná.
DOTAZ_KONCE = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX ann: <http://publications.europa.eu/ontology/annotation#>
SELECT ?celex ?konec ?poznamka WHERE {
  ?akt cdm:resource_legal_id_celex ?celex ;
       cdm:resource_legal_date_end-of-validity ?konec .
  FILTER(STRSTARTS(STR(?celex), "3"))
  OPTIONAL {
    ?ax owl:annotatedSource ?akt ;
        owl:annotatedProperty cdm:resource_legal_date_end-of-validity ;
        owl:annotatedTarget ?konec ;
        ann:comment_on_date ?poznamka .
  }
}
"""

BEZ_KONCE = "2100-01-01"
# Pozměňovací a rušicí předpis, který svůj účel splnil, vede EUR-Lex jako neplatný bez data.
NEUVEDENO = "neuvedeno"
# Kódy z číselníku fd_330: „Partial end of validity“, „Partial replacement“.
CASTECNY = ("{FIN/VAL/PART|", "{REMPLPART|")
# „Repealed by“, „Implicitly repealed by“, „Repealed and replaced by“, „Replaced by“.
RUSI = re.compile(r"\{(?:A|AI|AR|R)/PAR\|[^}]*\}\s*(3\d{4}[A-Z]{1,2}\d{4}(?:R?\(\d{2}\))*)")
# Poznámka, která říká, čím předpis skončil: zrušení, zastarání, konec lhůty nebo sezóny.
# „V“ (viz) a „L“ (souvisí s) jen odkazují jinam a proti příznaku platnosti neobstojí.
DOLOZENY = ("{A/PAR|", "{AI/PAR|", "{AR/PAR|", "{R/PAR|", "{CADUC|", "{FIN/")

# 32015L2366R(01) opravuje 32015L2366; přípon může být víc, 31999L0031R(02)R(01).
OPRAVA = re.compile(r"(?:R\(\d{2}\))+$")

KLICE = ("zruseno_k", "pozbude_platnosti_k", "pozbude_zcasti", "zrusi")
TAG = "  - zruseno"
ZRUSENO = "> [!danger] Pozbylo platnosti"
POZBUDE = "> [!warning] Pozbude platnosti"


def sparql(dotaz: str) -> list[dict[str, str]]:
    url = SPARQL + "?" + urllib.parse.urlencode(
        {"query": dotaz, "format": "application/sparql-results+json"})
    pozadavek = urllib.request.Request(url, headers={"User-Agent": "czech-law-md"})
    with urllib.request.urlopen(pozadavek, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [{k: v["value"] for k, v in b.items()} for b in data["results"]["bindings"]]


def nacti_cellar() -> tuple[dict[str, bool], dict[str, dict[str, set[str]]]]:
    """Příznak platnosti a ke každému předpisu data konce platnosti s poznámkami."""
    platny = {r["celex"]: r["platny"] in ("1", "true") for r in sparql(DOTAZ_PLATNY)}
    konce: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in sparql(DOTAZ_KONCE):
        konce[r["celex"]][r["konec"][:10]].add(r.get("poznamka", ""))
    return platny, konce


def rusici(konce: dict[str, set[str]], kdy: str) -> str:
    """CELEX předpisu, který konec způsobil — přednostně z poznámky k tomu datu."""
    for poznamky in (konce.get(kdy, set()), *konce.values()):
        for poznamka in sorted(poznamky):
            if m := RUSI.search(poznamka):
                return m.group(1)
    return ""


def urci(platny: bool | None, konce: dict[str, set[str]], dnes: str) -> dict[str, str]:
    """Co o konci platnosti předpisu říct. Klíče jsou pole frontmatteru; prázdno = nic."""
    def ma(d: str, kody: tuple[str, ...]) -> bool:
        return any(k in p for p in konce[d] for k in kody)

    data = sorted(d for d in konce if d < BEZ_KONCE)
    castecne = {d for d in data if ma(d, CASTECNY)}
    minule = [d for d in data if d < dnes]  # datum je poslední den platnosti
    minule_dolozene = [d for d in minule if d not in castecne and ma(d, DOLOZENY)]

    if minule and (platny is False or minule_dolozene):
        return {"zruseno_k": minule[-1] if platny is False else minule_dolozene[-1]}
    if platny is False and not data:
        return {"zruseno_k": NEUVEDENO}

    budouci = [d for d in data if d >= dnes]
    if not budouci:
        return {}
    budouci_cele = [d for d in budouci if d not in castecne]
    kdy = budouci_cele[0] if budouci_cele else budouci[0]
    zaznam = {"pozbude_platnosti_k": kdy}
    if not budouci_cele:
        zaznam["pozbude_zcasti"] = "true"
    if cim := rusici(konce, kdy):
        zaznam["zrusi"] = cim
    return zaznam


def s_opravami(prehled: dict[str, dict[str, str]], celexy) -> dict[str, dict[str, str]]:
    """Oprava (corrigendum) sdílí osud opravovaného předpisu. Vlastní údaj o platnosti v CELLARu
    nemá, takže se 457 oprav zrušených předpisů ve výsledcích tvářilo jako živé právo."""
    vysledek = dict(prehled)
    for celex in celexy:
        zaklad = OPRAVA.sub("", celex)
        if celex not in vysledek and zaklad != celex and zaklad in prehled:
            vysledek[celex] = prehled[zaklad]
    return vysledek


def bez_varovani(telo: str) -> str:
    """Odstraní jen vlastní callouty nad textem, ať se při opakovaném běhu nevrší."""
    radky = telo.split("\n")
    while radky and radky[0] in (ZRUSENO, POZBUDE):
        radky.pop(0)
        while radky and radky[0].startswith(">"):
            radky.pop(0)
        while radky and not radky[0].strip():
            radky.pop(0)
    return "\n".join(radky)


def varovani(zaznam: dict[str, str], eli: str, odkaz: Callable[[str], str]) -> str:
    # Varování patří nad text, protože kdo si vytáhne článek grepem, frontmatter nevidí.
    if "zruseno_k" in zaznam:
        kdy = "(EUR-Lex datum neuvádí)" if zaznam["zruseno_k"] == NEUVEDENO else f"k {zaznam['zruseno_k']}"
        return (f"{ZRUSENO}\n"
                f"> Tento předpis pozbyl platnosti {kdy} a **nelze podle něj postupovat**.\n"
                "> Zůstává tu kvůli posouzení právních vztahů vzniklých v době jeho platnosti.\n\n")
    if "pozbude_platnosti_k" not in zaznam:
        return ""
    kdy = zaznam["pozbude_platnosti_k"]
    cim = odkaz(zaznam["zrusi"]) if "zrusi" in zaznam else ""
    if "pozbude_zcasti" in zaznam:
        return (f"{POZBUDE}\n"
                f"> Tento předpis platí, ale **jeho část platí jen do {kdy}**"
                f"{f' (ruší ji {cim})' if cim else ''}. Které části se to týká, uvádí EUR-Lex: {eli}\n\n")
    return (f"{POZBUDE}\n"
            f"> Tento předpis platí jen **do {kdy}**{f', ruší ho {cim}' if cim else ''}. "
            "U vztahů, které to datum přesáhnou, počítej s tím, co platí potom.\n\n")


def uprav(text: str, zaznam: dict[str, str], odkaz: Callable[[str], str] = str) -> str:
    """Nastaví značky konce platnosti podle záznamu a ty, které už neplatí, odstraní."""
    if not text.startswith("---\n"):
        return text
    konec = text.find("\n---\n", 4)
    if konec == -1:
        return text

    vlastni = tuple(f"{k}:" for k in KLICE)
    hlavicka = [r for r in text[4:konec].split("\n") if not r.startswith(vlastni) and r != TAG]
    telo = bez_varovani(text[konec + 5:])
    eli = next((r[5:] for r in hlavicka if r.startswith("eli: ")), "")

    if "zruseno_k" in zaznam and "  - eu" in hlavicka:
        hlavicka.insert(hlavicka.index("  - eu") + 1, TAG)
    hlavicka += [f"{k}: {zaznam[k]}" for k in KLICE if k in zaznam]
    return "---\n" + "\n".join(hlavicka) + "\n---\n" + varovani(zaznam, eli, odkaz) + telo


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nahled", action="store_true", help="jen spočítat, nic nezapisovat")
    args = p.parse_args()

    if not EU.exists():
        print("složka eu/ neexistuje")
        return 0

    print("  ptám se CELLARu na platnost a konce platnosti …", flush=True)
    platny, konce = nacti_cellar()
    print(f"  předpisů s příznakem platnosti: {len(platny):,}, s datem konce: {len(konce):,}")

    v_repozitari = {f.stem: f for f in EU.rglob("*.md")}
    dnes = datetime.date.today().isoformat()
    prehled = {}
    for celex in v_repozitari:
        if zaznam := urci(platny.get(celex), konce.get(celex, {}), dnes):
            prehled[celex] = zaznam
    prehled = s_opravami(prehled, v_repozitari)
    zruseno = sum(1 for z in prehled.values() if "zruseno_k" in z)
    print(f"  v repozitáři zrušených: {zruseno:,}, s blížícím se koncem: {len(prehled) - zruseno:,}")

    def odkaz(celex: str) -> str:
        return f"[[{celex}]]" if celex in v_repozitari else celex

    upraveno = 0
    for celex, cesta in sorted(v_repozitari.items()):
        puvodni = cesta.read_text(encoding="utf-8")
        novy = uprav(puvodni, prehled.get(celex, {}), odkaz)
        if novy != puvodni:
            upraveno += 1
            if not args.nahled:
                cesta.write_text(novy, encoding="utf-8")
    print(f"  {'dotklo by se' if args.nahled else 'upraveno'}: {upraveno:,} souborů")

    if not args.nahled:
        zrusene = {c: z["zruseno_k"] for c, z in prehled.items() if "zruseno_k" in z}
        konci = {c: z for c, z in prehled.items() if "zruseno_k" not in z}
        PREHLED.parent.mkdir(parents=True, exist_ok=True)
        for cesta, obsah in ((PREHLED, zrusene), (POZBUDE_JSON, konci)):
            cesta.write_text(json.dumps(obsah, ensure_ascii=False, indent=1, sort_keys=True),
                             encoding="utf-8")
        print(f"  strojový přehled: {PREHLED} a {POZBUDE_JSON.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
