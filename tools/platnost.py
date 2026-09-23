"""Doplní do předpisů informaci o zrušení.

Samotný text předpisu neříká, že už neplatí — zrušený zákon vypadá v e-Sbírce stejně jako živý.

Datum zrušení se bere z dávky `006PravniAktMetadata`, kde je jako přímý údaj
(`metadata-datum-zrušení`, náhradně `metadata-datum-účinnosti-do`). Dávka `007` s vazbami
`ZRUSPRED` slouží jen k dohledání, **čím** byl předpis zrušen — sama o sobě je totiž děravá:
zná 8 907 zrušení, kdežto 006 jich má 16 389 a obsahuje všechna z nich.

Do frontmatteru se doplní `zruseno_k` a `zrusil`, přibude tag `zruseno` a nad text varovný
callout, protože kdo si vytáhne paragraf grepem, hlavičku souboru nikdy neuvidí. Strojově je
totéž v `.zmeny/platnost.json`, aby index a MCP nemusely číst velké dávky.

**Pozor na obrácený závěr.** Že předpis záznam o zrušení nemá, pořád neznamená, že platí —
jen že e-Sbírka jeho konec neeviduje. Zrušení je spolehlivé, platnost je nevyvrácená domněnka.

    python3 tools/platnost.py            # doplní podle stažených dávek
    python3 tools/platnost.py --nahled   # jen řekne, čeho by se to týkalo
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
ZAKONY = KOREN / "zakony"
SMLOUVY = KOREN / "smlouvy"
CACHE = KOREN / ".cache"
PLATNOST = KOREN / ".zmeny" / "platnost.json"

DAVKY = {
    "006": "006PravniAktMetadata.json.gz",
    "007": "007PravniAktKonsolidacniVazba.json.gz",
}

# esel-esb:eli/cz/sb/1991/513/2013-07-01/dokument -> ('sb', '1991', '513')
ELI = re.compile(r"eli/cz/(\w+)/(\d{4})/([\w-]+)/")


def nacti_davku(jmeno: str) -> list:
    # Stejná cache se stářím jako u stahování: bez ní by lokální běh viděl zrušení jen
    # z prvního dne, kdy se dataset stáhl.
    from stahni import stahni_dataset  # noqa: PLC0415
    cesta = stahni_dataset(DAVKY[jmeno], f"{jmeno}.json.gz")
    with gzip.open(cesta, "rt", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("položky") or data.get("polozky") or data


def cim_zruseno() -> dict[str, str]:
    """Mapa 'řada/číslo/rok' -> citace rušícího předpisu, z vazeb ZRUSPRED."""
    cim: dict[str, str] = {}
    for v in nacti_davku("007"):
        if v.get("cis-esb-typ-novelizační-instrukce-položka") != "ZRUSPRED":
            continue
        zdroj = ELI.search((v.get("znění-fragment-zdroj") or {}).get("iri", ""))
        novela = ELI.search((v.get("znění-fragment-novela") or {}).get("iri", ""))
        if zdroj and novela:
            # Řada patří do klíče: 1/2000 Sb. a 1/2000 Sb. m. s. jsou dva různé předpisy.
            cim[f"{zdroj.group(1)}/{zdroj.group(3)}/{zdroj.group(2)}"] = \
                f"{novela.group(3)}/{novela.group(2)} Sb."
    return cim


def nacti_zruseni() -> dict[str, dict[str, str]]:
    """Mapa citace -> {k, cim}. Datum z metadat, rušící předpis z vazeb."""
    cim = cim_zruseno()
    zruseni: dict[str, dict[str, str]] = {}
    for v in nacti_davku("006"):
        kdy = v.get("metadata-datum-zrušení") or v.get("metadata-datum-účinnosti-do")
        if not kdy:
            continue
        citace = v.get("akt-citace") or ""
        eli = ELI.search((v.get("akt-iri") or "") + "/")
        klic = f"{eli.group(1)}/{eli.group(3)}/{eli.group(2)}" if eli else ""
        zruseni[citace] = {"k": kdy, "cim": cim.get(klic, "")}
    return zruseni


VAROVANI = "> [!danger] Zrušeno"


def uprav_telo(telo: str, zaznam: dict[str, str] | None) -> str:
    """Varování hned pod nadpis předpisu. Frontmatter na to nestačí — kdo si vytáhne paragraf
    grepem nebo awkem, hlavičku souboru nikdy neuvidí."""
    radky = telo.split("\n")
    # Staré varování pryč, ať se při opakovaném běhu nevrší — ale jen vlastní. Upozornění na
    # budoucí znění stojí na stejném místě a mazat ho by znamenalo ztratit ho každým během.
    if radky and radky[0] == VAROVANI:
        while radky and radky[0].startswith(">"):
            radky.pop(0)
        while radky and not radky[0].strip():
            radky.pop(0)
    if not zaznam:
        return "\n".join(radky)

    kdo = f" předpisem {zaznam['cim']}" if zaznam["cim"] else ""
    blok = [
        VAROVANI,
        f"> Tento předpis byl zrušen k {zaznam['k']}{kdo} a **nelze podle něj postupovat**.",
        "> Zůstává tu kvůli posouzení právních vztahů vzniklých v době jeho účinnosti.",
        "",
    ]
    return "\n".join(blok + radky)


def uprav_frontmatter(text: str, zaznam: dict[str, str] | None) -> str:
    """Vloží nebo odstraní `zruseno_k`, `zrusil` a tag `zruseno`. Idempotentní."""
    if not text.startswith("---\n"):
        return text
    konec = text.find("\n---\n", 4)
    if konec == -1:
        return text

    hlavicka = text[4:konec].split("\n")
    telo = text[konec + 5:]

    ocistene = [r for r in hlavicka if not r.startswith(("zruseno_k:", "zrusil:")) and r.strip() != "- zruseno"]
    telo = uprav_telo(telo, zaznam)
    if not zaznam:
        return "---\n" + "\n".join(ocistene) + "\n---\n" + telo

    # klíče patří nad `tags:`, tag mezi ostatní tagy
    kam = next((i for i, r in enumerate(ocistene) if r.startswith("tags:")), len(ocistene))
    vlozit = [f"zruseno_k: {zaznam['k']}"]
    if zaznam["cim"]:
        vlozit.append(f"zrusil: {json.dumps(zaznam['cim'], ensure_ascii=False)}")
    nove = ocistene[:kam] + vlozit + ocistene[kam:]
    if any(r.startswith("tags:") for r in nove):
        nove.insert(next(i for i, r in enumerate(nove) if r.startswith("tags:")) + 1, "  - zruseno")

    return "---\n" + "\n".join(nove) + "\n---\n" + telo


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nahled", action="store_true")
    args = p.parse_args()

    zruseni = nacti_zruseni()
    print(f"vazeb o zrušení: {len(zruseni):,}")

    dotceno = zruseno_celkem = 0
    prehled: dict[str, dict[str, str]] = {}

    for cesta in sorted(ZAKONY.rglob("*.md")) + sorted(SMLOUVY.rglob("*.md")):
        puvodni = cesta.read_text(encoding="utf-8")
        # Citace se bere z frontmatteru, ne skládáním z názvu souboru — je to přesné
        # a nevyžaduje hádat, jak se která řada píše.
        m = re.search(r"^citace: (.+)$", puvodni, re.M)
        citace = m.group(1).strip().strip('"') if m else ""

        zaznam = zruseni.get(citace)
        if zaznam:
            zruseno_celkem += 1
            prehled[citace] = zaznam

        novy = uprav_frontmatter(puvodni, zaznam)
        if novy != puvodni:
            dotceno += 1
            if not args.nahled:
                cesta.write_text(novy, encoding="utf-8")

    celkem = sum(1 for _ in ZAKONY.rglob("*.md")) + sum(1 for _ in SMLOUVY.rglob("*.md"))
    print(f"předpisů: {celkem:,}, z toho zrušených: {zruseno_celkem:,} ({100 * zruseno_celkem / max(celkem, 1):.0f} %)")
    print(f"{'dotklo by se' if args.nahled else 'upraveno'}: {dotceno:,} souborů")

    if not args.nahled:
        PLATNOST.parent.mkdir(parents=True, exist_ok=True)
        PLATNOST.write_text(json.dumps(prehled, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        print(f"strojový přehled: {PLATNOST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
