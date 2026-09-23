"""Srovná čísla v dokumentaci se skutečností.

Kolik je v repozitáři předpisů se mění každý týden, ale README a Průvodce to tvrdí natvrdo.
Bez tohohle se čísla tiše rozejdou a dokumentace začne lhát — při auditu se našly tři taková
místa. Skript je umí zkontrolovat i opravit.

    python3 tools/cisla.py            # vypíše, co nesedí (nenulový kód = nález)
    python3 tools/cisla.py --oprav    # přepíše je podle skutečnosti
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
DB = KOREN / ".cache" / "index.db"

# Značka v textu -> jak se spočítá. Značka musí být v dokumentaci jednoznačná.
MISTA = {
    "predpisy": (r"\| \*\*Sbírka zákonů\*\* \| ([\d\s]+) předpisů",
                 lambda: len(list((KOREN / "zakony").rglob("*.md")))),
    "smlouvy": (r"\| \*\*Mezinárodní smlouvy\*\* \| ([\d\s]+) \|",
                lambda: len(list((KOREN / "smlouvy").rglob("*.md")))),
    "eu": (r"\| \*\*Právo EU\*\* \| ([\d\s]+) nařízení",
           lambda: len(list((KOREN / "eu").rglob("*.md")))),
    "judikatura_par": (r"rozhodnutí, u (\d[\d\s]*)\s*paragrafů",
                       lambda: len(list((KOREN / "judikatura").rglob("*.md")))),
    "predpisy_celkem": (r"\| dohromady \| ([\d\s]+) předpisů",
                        lambda: _z_indexu("SELECT count(*) FROM predpis")),
    "ustanoveni": (r"\| dohromady \| [\d\s]+ předpisů, ([\d\s]+) ustanovení",
                   lambda: _z_indexu("SELECT count(*) FROM usek")),
    "zrusene": (r"\*\*Údaj o zrušení\*\* u (\d[\d\s]*)\s*předpisů",
                lambda: _z_indexu("SELECT count(*) FROM predpis WHERE zruseno_k != ''")),
    # Vzory musí snést zalomení řádku, dokumentace se láme na 100 znaků.
    "uplna_zneni": (r"\((\d[\d\s]*)\s*publikací textu jiného zákona",
                    lambda: _z_indexu("SELECT count(*) FROM predpis WHERE uplne_zneni = 1")),
}


def _z_indexu(dotaz: str) -> int | None:
    if not DB.exists():
        return None
    spoj = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return spoj.execute(dotaz).fetchone()[0]
    except sqlite3.DatabaseError:
        return None
    finally:
        spoj.close()


def s_mezerami(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def zkontroluj(oprav: bool) -> int:
    nalezy = 0
    for soubor in ("README.md", "Průvodce.md"):
        cesta = KOREN / soubor
        if not cesta.exists():
            continue
        text = puvodni = cesta.read_text(encoding="utf-8")

        for jmeno, (vzor, spocitej) in MISTA.items():
            m = re.search(vzor, text)
            if not m:
                continue
            skutecnost = spocitej()
            if skutecnost is None:
                print(f"  {soubor}: {jmeno} nelze ověřit (index neexistuje)")
                continue
            tvrzeni = m.group(1).strip()
            spravne = s_mezerami(skutecnost)
            if tvrzeni.replace(" ", "") == str(skutecnost):
                continue
            nalezy += 1
            print(f"  ✗ {soubor}: {jmeno} tvrdí {tvrzeni}, je {spravne}")
            if oprav:
                # Skupina v některých vzorech zachytí i mezeru za číslem; ta musí zůstat.
                konec = m.start(1) + len(m.group(1).rstrip())
                text = text[:m.start(1)] + spravne + text[konec:]

        if oprav and text != puvodni:
            cesta.write_text(text, encoding="utf-8")
            print(f"  {soubor} opraven")

    if not nalezy:
        print("  čísla v dokumentaci sedí")
    return nalezy


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--oprav", action="store_true", help="přepsat čísla podle skutečnosti")
    args = p.parse_args()
    nalezy = zkontroluj(args.oprav)
    return 0 if args.oprav or not nalezy else 1


if __name__ == "__main__":
    sys.exit(main())
