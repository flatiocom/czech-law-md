"""Vyzkouší různé váhy řazení proti zlaté sadě a vypíše, která je nejlepší.

Řazení míchá dvě věci: relevanci z bm25 a autoritu předpisu (kolik rozhodnutí se ho dovolává).
Poměr mezi nimi byl odhadnutý, ne změřený — a ukázalo se, že autorita umí přebít relevanci:
na dotaz po příjmech ze závislé činnosti vyhrál § 277 občanského soudního řádu, protože má
autoritu 13,1, zatímco § 6 zákona o daních z příjmů jen 5,0.

Skript sahá rovnou do SQL, takže nepotřebuje přestavovat index a jedno kolo trvá vteřiny.

    python3 tools/ladeni.py               # projede váhy autority
    python3 tools/ladeni.py --vahy-fts    # projede i váhy sloupců fulltextu
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
DB = KOREN / ".cache" / "index.db"

sys.path.insert(0, str(KOREN / "tools"))
from dotazy import SADA  # noqa: E402

LIMIT = 10


def zmer(spoj: sqlite3.Connection, vaha_autority: float, fts: tuple[float, ...]) -> tuple[int, int, int]:
    """Vrací (ustanovení první, ustanovení v desítce, správný předpis první) přes celou sadu."""
    sloupce = ", ".join(str(v) for v in fts)
    sql = f"""
        SELECT p.citace, u.oznaceni
        FROM usek_fts JOIN usek u ON u.id = usek_fts.rowid
        JOIN predpis p ON p.citace = u.predpis
        WHERE usek_fts MATCH ? AND (p.zruseno_k IS NULL OR p.zruseno_k = '') AND p.uplne_zneni = 0
        ORDER BY p.uplne_zneni, bm25(usek_fts, {sloupce}) - ? * COALESCE(p.autorita, 0)
        LIMIT {LIMIT}
    """
    prvni = v_desitce = predpis_prvni = 0
    for _, lidsky, agenti, cil_predpis, cil_par in SADA:
        for dotaz in (lidsky, agenti):
            try:
                zasahy = spoj.execute(sql, (dotaz, vaha_autority)).fetchall()
            except sqlite3.OperationalError:
                continue
            if not zasahy:
                continue
            if zasahy[0][0] == cil_predpis:
                predpis_prvni += 1
            for i, (c, o) in enumerate(zasahy):
                if c == cil_predpis and o.replace(" ", "") == cil_par.replace(" ", ""):
                    v_desitce += 1
                    if i == 0:
                        prvni += 1
                    break
    return prvni, v_desitce, predpis_prvni


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vahy-fts", action="store_true", help="zkusit i váhy sloupců fulltextu")
    args = p.parse_args()

    spoj = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    celkem = 2 * len(SADA)
    zaklad = (4.0, 3.0, 1.0, 1.0, 1.0)

    print(f"  sada: {len(SADA)} dotazů ve dvou podobách = {celkem} pokusů\n")
    print(f"  {'váha autority':>14} {'§ první':>9} {'§ v desítce':>12} {'předpis první':>14}")
    nej = None
    for vaha in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5):
        prvni, desitka, predpis = zmer(spoj, vaha, zaklad)
        znak = ""
        if nej is None or desitka + predpis > nej[1]:
            nej, znak = (vaha, desitka + predpis), "  <-"
        print(f"  {vaha:>14.2f} {prvni:>4}/{celkem} {desitka:>7}/{celkem} {predpis:>9}/{celkem}{znak}")
    print(f"\n  nejlepší váha autority: {nej[0]}")

    if args.vahy_fts:
        print(f"\n  {'váhy sloupců':>26} {'§ první':>9} {'§ v desítce':>12} {'předpis první':>14}")
        for fts in ((4.0, 3.0, 1.0, 1.0, 1.0), (4.0, 3.0, 1.0, 0.5, 1.0), (4.0, 3.0, 1.0, 2.0, 1.0),
                    (2.0, 3.0, 1.0, 1.0, 1.0), (8.0, 3.0, 1.0, 1.0, 1.0), (4.0, 3.0, 1.0, 1.0, 0.5),
                    (4.0, 3.0, 1.0, 1.0, 2.0)):
            prvni, desitka, predpis = zmer(spoj, nej[0], fts)
            print(f"  {str(fts):>26} {prvni:>4}/{celkem} {desitka:>7}/{celkem} {predpis:>9}/{celkem}")

    spoj.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
