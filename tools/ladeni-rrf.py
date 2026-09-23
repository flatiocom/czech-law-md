"""Porovná dnešní sekvenční fallback s Reciprocal Rank Fusion na zlaté sadě.

Dnes se varianty dotazu (přesný, lemmatizovaný, zkrácený na kmeny) zkouší po řadě a použije se
první, která něco vrátí. Slabina: když přesný dotaz vrátí jediný špatný zásah, k lemmatizovanému
se nikdy nedojde.

RRF (Cormack a spol., 2009) je v information retrieval obvyklý způsob, jak slít víc seřazených
seznamů dohromady: každý zásah dostane 1/(k + pořadí) z každého seznamu, ve kterém se objevil,
a body se sečtou. Nepotřebuje, aby skóre různých variant byla srovnatelná — pracuje jen s pořadím.

    python3 tools/ladeni-rrf.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
DB = KOREN / ".cache" / "index.db"
sys.path.insert(0, str(KOREN / "tools"))

from dotazy import SADA  # noqa: E402
from mcp import VLASTNI_SYNTAXE, lemmatizuj_dotaz, zkrat_na_kmeny  # noqa: E402

LIMIT = 10
SQL = """
    SELECT p.citace, u.oznaceni
    FROM usek_fts JOIN usek u ON u.id = usek_fts.rowid
    JOIN predpis p ON p.citace = u.predpis
    WHERE usek_fts MATCH ? AND (p.zruseno_k IS NULL OR p.zruseno_k = '') AND p.uplne_zneni = 0
    ORDER BY p.uplne_zneni, bm25(usek_fts, 4.0, 3.0, 1.0, 1.0, 2.0) - COALESCE(p.autorita, 0)
    LIMIT ?
"""


def varianty(dotaz: str) -> list[str]:
    """Dotaz tak, jak se dá položit: doslova, přes základní tvary, přes kmeny."""
    v = [dotaz]
    if not any(z in dotaz for z in VLASTNI_SYNTAXE):
        if (zaklady := lemmatizuj_dotaz(dotaz)):
            v.append(zaklady)
        v.append(zkrat_na_kmeny(dotaz))
    return v


def spust(spoj: sqlite3.Connection, dotaz: str, limit: int) -> list[tuple[str, str]]:
    try:
        return spoj.execute(SQL, (dotaz, limit)).fetchall()
    except sqlite3.OperationalError:
        return []


def sekvencne(spoj: sqlite3.Connection, dotaz: str) -> list[tuple[str, str]]:
    for v in varianty(dotaz):
        if (zasahy := spust(spoj, v, LIMIT)):
            return zasahy
    return []


def rrf(spoj: sqlite3.Connection, dotaz: str, k: int = 60) -> list[tuple[str, str]]:
    body: dict[tuple[str, str], float] = {}
    for v in varianty(dotaz):
        for poradi, zasah in enumerate(spust(spoj, v, 30), start=1):
            body[zasah] = body.get(zasah, 0.0) + 1.0 / (k + poradi)
    return [z for z, _ in sorted(body.items(), key=lambda x: -x[1])][:LIMIT]


def zmer(spoj: sqlite3.Connection, hledaci) -> tuple[int, int, int, int]:
    prvni = v_desitce = predpis_prvni = predpis_kdekoli = 0
    for _, lidsky, agenti, cil_p, cil_par in SADA:
        for dotaz in (lidsky, agenti):
            zasahy = hledaci(spoj, dotaz)
            if not zasahy:
                continue
            if zasahy[0][0] == cil_p:
                predpis_prvni += 1
            if any(c == cil_p for c, _ in zasahy):
                predpis_kdekoli += 1
            for i, (c, o) in enumerate(zasahy):
                if c == cil_p and o.replace(" ", "") == cil_par.replace(" ", ""):
                    v_desitce += 1
                    if i == 0:
                        prvni += 1
                    break
    return prvni, v_desitce, predpis_prvni, predpis_kdekoli


def main() -> int:
    spoj = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    celkem = 2 * len(SADA)
    print(f"  sada: {len(SADA)} dotazů ve dvou podobách = {celkem} pokusů\n")
    print(f"  {'způsob':<22} {'§ první':>9} {'§ v desítce':>12} {'předpis první':>14} {'předpis kdekoli':>16}")

    for popis, hledaci in (("dnes (sekvenčně)", sekvencne),
                           ("RRF k=60", lambda s, d: rrf(s, d, 60)),
                           ("RRF k=10", lambda s, d: rrf(s, d, 10)),
                           ("RRF k=200", lambda s, d: rrf(s, d, 200))):
        a, b, c, d_ = zmer(spoj, hledaci)
        print(f"  {popis:<22} {a:>4}/{celkem} {b:>7}/{celkem} {c:>9}/{celkem} {d_:>11}/{celkem}")

    spoj.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
