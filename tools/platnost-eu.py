"""Doplní k předpisům EU údaj o konci platnosti.

`platnost.py` bere zrušení z konsolidačních vazeb e-Sbírky, které o právu EU nic nevědí, takže
všech 24 026 nařízení a směrnic se ve výsledcích tvářilo jako živé právo — i směrnice 95/46/ES,
kterou v roce 2018 nahradilo GDPR.

CELLAR datum konce platnosti vydává (`resource_legal_date_end-of-validity`) a dá se získat
jedním SPARQL dotazem místo 24 tisíc stažení. Hodnota `9999-12-31` znamená „bez konce“.

    python3 tools/platnost-eu.py            # doplní značky do souborů
    python3 tools/platnost-eu.py --nahled   # jen spočítá, nic nezapíše
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
EU = KOREN / "eu"
PREHLED = KOREN / ".zmeny" / "platnost-eu.json"
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
TIMEOUT = 600

DOTAZ = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?celex ?konec WHERE {
  ?akt cdm:resource_legal_id_celex ?celex ;
       cdm:resource_legal_date_end-of-validity ?konec .
  FILTER(?konec < "2100-01-01"^^xsd:date)
  FILTER(STRSTARTS(STR(?celex), "3"))
}
"""

VAROVANI = ("> [!danger] Pozbylo platnosti\n"
            "> Tento předpis pozbyl platnosti k {kdy} a **nelze podle něj postupovat**.\n"
            "> Zůstává tu kvůli posouzení právních vztahů vzniklých v době jeho platnosti.\n\n")


def stahni_konce() -> dict[str, str]:
    url = SPARQL + "?" + urllib.parse.urlencode(
        {"query": DOTAZ, "format": "application/sparql-results+json"})
    pozadavek = urllib.request.Request(url, headers={"User-Agent": "czech-law-md"})
    with urllib.request.urlopen(pozadavek, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return {b["celex"]["value"]: b["konec"]["value"][:10]
            for b in data["results"]["bindings"]}


def doplnit(cesta: Path, kdy: str) -> bool:
    """Přidá do frontmatteru `zruseno_k` a nad text varování. Vrací, jestli se soubor změnil."""
    text = cesta.read_text(encoding="utf-8")
    if "zruseno_k:" in text.split("---", 2)[1]:
        return False

    konec = text.find("\n---\n", 4)
    if konec == -1:
        return False
    hlavicka, telo = text[:konec], text[konec + 5:]
    hlavicka += f"\nzruseno_k: {kdy}"
    hlavicka = hlavicka.replace("tags:\n  - eu", "tags:\n  - eu\n  - zruseno", 1)
    # Varování patří nad text, protože kdo si vytáhne článek grepem, frontmatter nevidí.
    return_text = f"{hlavicka}\n---\n{VAROVANI.format(kdy=kdy)}{telo}"
    cesta.write_text(return_text, encoding="utf-8")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nahled", action="store_true", help="jen spočítat, nic nezapisovat")
    args = p.parse_args()

    if not EU.exists():
        print("složka eu/ neexistuje")
        return 0

    print("  ptám se CELLARu na konce platnosti …", flush=True)
    konce = stahni_konce()
    print(f"  předpisů s koncem platnosti: {len(konce):,}")

    v_repozitari = {f.stem: f for f in EU.rglob("*.md")}
    dotcene = {c: k for c, k in konce.items() if c in v_repozitari}
    print(f"  z toho v repozitáři: {len(dotcene):,}")

    if args.nahled:
        for celex, kdy in sorted(dotcene.items())[:5]:
            print(f"    {celex}: do {kdy}")
        return 0

    upraveno = 0
    for celex, kdy in dotcene.items():
        if doplnit(v_repozitari[celex], kdy):
            upraveno += 1

    PREHLED.parent.mkdir(parents=True, exist_ok=True)
    PREHLED.write_text(json.dumps(dotcene, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
    print(f"  upraveno: {upraveno:,} souborů")
    print(f"  strojový přehled: {PREHLED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
