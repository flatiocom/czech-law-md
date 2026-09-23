"""Jednorázová oprava: nadpis paragrafu přesune pod jeho označení.

Původní převod psal `###### § 1724 — Obecná ustanovení`, takže odkaz `[[89-2012#§ 1724]]`
v Obsidianu nenašel cíl — kotva na nadpis musí sedět přesně. Nově převod píše označení a nadpis
na dva řádky; tenhle skript srovná soubory, které vznikly předtím, aby se kvůli tomu nemusela
znovu stahovat celá sbírka.

    python3 tools/oprav-kotvy.py --nahled   # kolik souborů se dotkne
    python3 tools/oprav-kotvy.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
ZAKONY = KOREN / "zakony"

# "###### § 1724 — Obecná ustanovení" -> označení a nadpis zvlášť
SLOUCENY = re.compile(r"^(#{2,6}) (§ [^\s—]+|Čl\. [^\s—]+|[^\n—]{1,60}?) — (.+)$", re.M)


def oprav(text: str) -> tuple[str, int]:
    pocet = 0

    def nahrad(m: re.Match[str]) -> str:
        nonlocal pocet
        pocet += 1
        return f"{m.group(1)} {m.group(2)}\n**{m.group(3)}**"

    return SLOUCENY.sub(nahrad, text), pocet


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nahled", action="store_true")
    args = p.parse_args()

    souboru = nadpisu = 0
    for cesta in sorted(ZAKONY.rglob("*.md")):
        puvodni = cesta.read_text(encoding="utf-8")
        novy, pocet = oprav(puvodni)
        if pocet:
            souboru += 1
            nadpisu += pocet
            if not args.nahled:
                cesta.write_text(novy, encoding="utf-8")

    sloveso = "dotkne se" if args.nahled else "opraveno"
    print(f"{sloveso} {souboru:,} souborů, {nadpisu:,} nadpisů")
    return 0


if __name__ == "__main__":
    sys.exit(main())
