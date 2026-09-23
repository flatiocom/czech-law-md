"""Zapíše do CHANGELOG.md, co se od minula změnilo, a připraví přehled pro agenta.

Smysl je ten, aby po aktualizaci nemusel nikdo procházet strom složek. `CHANGELOG.md` je lidský
zápis po dnech; `.zmeny/posledni.json` je strojový výstup téhož běhu, ze kterého agent zjistí
seznam dotčených předpisů a paragrafů, aniž by cokoli otevíral.

Změny se neodhadují — e-Sbírka u každého znění vede `datum-čas-poslední-změny` a v dávce
konsolidačních vazeb je i to, který předpis který novelizoval. Diff v gitu pak ukáže, kterých
paragrafů se to dotklo.

    python3 tools/changelog.py            # zapíše změny od posledního commitu
    python3 tools/changelog.py --nahled   # jen vypíše, nic nezapisuje
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
CACHE = KOREN / ".cache"
CHANGELOG = KOREN / "CHANGELOG.md"
ZMENY = KOREN / ".zmeny"

PARAGRAF = re.compile(r"^[+-]#{2,6}\s+(§\s*[\w/]+|Čl\.\s*[\w/]+)")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(KOREN), *args], capture_output=True, text=True).stdout.strip()


def zmenene_soubory() -> dict[str, str]:
    """Cesta → stav (A přidáno, M změněno, D smazáno) proti poslednímu commitu."""
    if not (KOREN / ".git").exists():
        return {}
    # Sledovat jen zakony/ znamenalo, že změny v právu EU a ve smlouvách se do changelogu
    # nedostaly — a to jsou dvě třetiny předpisů v repozitáři.
    vystup = git("status", "--porcelain", "--", "zakony", "smlouvy", "eu")
    soubory = {}
    for radek in vystup.splitlines():
        if not radek.strip():
            continue
        # Porcelain má dva znaky stavu, mezeru a cestu; cesta s mezerou nebo diakritikou
        # přijde v uvozovkách. Dělit napevno na třetím znaku useklo „zakony“ na „akony“.
        stav, _, cesta = radek[:2].strip(), radek[2:3], radek[3:].strip().strip('"')
        soubory[cesta] = "A" if "?" in stav or "A" in stav else ("D" if "D" in stav else "M")
    return soubory


def dotcene_paragrafy(cesta: str) -> list[str]:
    """Paragrafy, jejichž řádek se v diffu objevil — bez otevírání celého předpisu."""
    diff = git("diff", "--unified=0", "--", cesta)
    nalezene = []
    for radek in diff.splitlines():
        m = PARAGRAF.match(radek)
        if m and m.group(1) not in nalezene:
            nalezene.append(m.group(1))
    return nalezene


ELI_Z_IRI = re.compile(r"eli/cz/(\w+)/(\d{4})/(\d+\w*)/")


def citace_z_iri(iri: str) -> str:
    """`esel-esb:eli/cz/sb/2012/89/2026-01-01/dokument/…` -> `89/2012 Sb.`"""
    m = ELI_Z_IRI.search(iri or "")
    if not m:
        return ""
    rada = {"sb": "Sb.", "sm": "Sb. m. s."}.get(m.group(1), m.group(1))
    return f"{m.group(3)}/{m.group(2)} {rada}"


def novelizace(pro: set[str]) -> dict[str, list[str]]:
    """Které předpisy novelizovaly ty v `pro`. Vazba je fragment novely -> fragment cíle, takže
    se z obou IRI vytáhne citace aktu a zbytek se zahodí."""
    cesta = CACHE / "007.json.gz"
    if not cesta.exists() or not pro:
        return {}
    with gzip.open(cesta, "rt", encoding="utf-8") as f:
        polozky = json.load(f).get("položky", [])

    vazby: dict[str, list[str]] = defaultdict(list)
    for v in polozky:
        cil = citace_z_iri((v.get("znění-fragment-cíl") or {}).get("iri", ""))
        if cil not in pro:
            continue
        novela = citace_z_iri((v.get("znění-fragment-novela") or {}).get("iri", ""))
        if novela and novela != cil and novela not in vazby[cil]:
            vazby[cil].append(novela)
    return vazby


def citace_z_cesty(cesta: str) -> str:
    # Předpis EU se necituje číslem a rokem, ale CELEXem.
    if cesta.startswith("eu/"):
        return Path(cesta).stem
    # Písmeno na začátku patří k číslu: n69/1968 Sb. (oznámení) je jiný předpis než 69/1968 Sb.
    # Takových je 6 214 a bez toho jim changelog dával popisek cizího předpisu.
    m = re.search(r"([a-zA-Z]*\d+\w*)-(\d{4})(?:-ms)?\.md$", cesta)
    if not m:
        return cesta
    rada = "Sb. m. s." if cesta.endswith("-ms.md") else "Sb."
    return f"{m.group(1)}/{m.group(2)} {rada}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nahled", action="store_true", help="jen vypíše, nic nezapíše")
    args = p.parse_args()

    soubory = zmenene_soubory()
    if not soubory:
        print("žádné změny v zakony/, smlouvy/ ani eu/")
        return 0

    stav = json.loads((CACHE / "stav.json").read_text(encoding="utf-8")) if (CACHE / "stav.json").exists() else {}
    nazvy = {citace: udaje.get("nazev", "") for citace, udaje in stav.items()}
    # Právo EU má vlastní stav a citaci mu dělá CELEX, ne číslo a rok.
    eu_stav = CACHE / "eu-stav.json"
    if eu_stav.exists():
        for celex, udaje in json.loads(eu_stav.read_text(encoding="utf-8")).items():
            nazvy[celex] = udaje.get("nazev", "") if isinstance(udaje, dict) else ""

    zmenene_citace = {citace_z_cesty(c) for c, d in soubory.items() if d == "M"}
    novely = novelizace(zmenene_citace)

    zaznamy = []
    for cesta, druh in sorted(soubory.items()):
        citace = citace_z_cesty(cesta)
        zaznamy.append({
            "novelizovano": novely.get(citace, [])[:6],
            "citace": citace,
            "nazev": nazvy.get(citace, ""),
            "druh": {"A": "nový", "M": "změněný", "D": "zrušený"}[druh],
            "soubor": cesta,
            "ucinnost_od": stav.get(citace, {}).get("ucinnost_od", ""),
            "paragrafy": dotcene_paragrafy(cesta) if druh == "M" else [],
        })

    dnes = date.today().isoformat()
    nove = [z for z in zaznamy if z["druh"] == "nový"]
    zmenene = [z for z in zaznamy if z["druh"] == "změněný"]

    radky = [f"## {dnes}", ""]
    radky.append(f"{len(nove)} nových, {len(zmenene)} změněných.")
    radky.append("")
    for z in zmenene:
        # Záznam občas nese cestu ke složce; wikilink na složku v Obsidianu nikam nevede.
        if not z["soubor"].endswith(".md"):
            continue
        odkaz = Path(z["soubor"]).stem
        popis = f"- **[[{odkaz}|{z['citace']}]]** {z['nazev']}".rstrip()
        if z["ucinnost_od"]:
            popis += f" — znění k {z['ucinnost_od']}"
        if z["paragrafy"]:
            popis += f"; dotčeno: {', '.join(z['paragrafy'][:12])}"
            if len(z["paragrafy"]) > 12:
                popis += f" a dalších {len(z['paragrafy']) - 12}"
        if z.get("novelizovano"):
            popis += f" (novelizuje: {', '.join(z['novelizovano'])})"
        radky.append(popis)
    if nove:
        radky.append("")
        radky.append(f"<details><summary>Nově přidáno ({len(nove)})</summary>")
        radky.append("")
        for z in nove[:200]:
            if not z["soubor"].endswith(".md"):
                continue
            radky.append(f"- [[{Path(z['soubor']).stem}|{z['citace']}]] {z['nazev']}".rstrip())
        if len(nove) > 200:
            radky.append(f"- … a dalších {len(nove) - 200}")
        radky.append("")
        radky.append("</details>")
    radky.append("")

    zapis = "\n".join(radky)
    if args.nahled:
        print(zapis)
        return 0

    hlavicka = (
        "# Změny\n\n"
        "Co se kdy změnilo ve sbírce. Zapisuje `tools/changelog.py` po každé aktualizaci;\n"
        "strojový tvar téhož je v `.zmeny/posledni.json`.\n\n"
    )
    stary = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
    telo = stary[len(hlavicka):] if stary.startswith(hlavicka) else stary
    CHANGELOG.write_text(hlavicka + zapis + "\n" + telo, encoding="utf-8")

    ZMENY.mkdir(exist_ok=True)
    (ZMENY / "posledni.json").write_text(
        json.dumps({"datum": dnes, "zaznamy": zaznamy}, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"zapsáno do CHANGELOG.md: {len(nove)} nových, {len(zmenene)} změněných")
    print(f"strojový přehled: {ZMENY / 'posledni.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
