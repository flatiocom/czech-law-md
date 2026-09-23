"""Sestaví rejstřík a Bases pohledy, aby se ve sbírce dalo hledat bez procházení složek.

Vznikají tři věci:

* `Rejstřík.md` — předpisy po letech, s wikilinky. Vstupní bod pro člověka i pro agenta.
* `Předpisy.base` — tabulka pro Obsidian Bases, filtrovatelná podle roku a druhu předpisu.
* `odkazy` ve frontmatteru každého předpisu — na které jiné předpisy se odkazuje.

Odkazy mezi předpisy jsou v dávce `008PravniAktOdkaz`, takže se nehádají z textu.

    python3 tools/rejstrik.py            # rejstřík a base
    python3 tools/rejstrik.py --odkazy   # navíc doplní odkazy do frontmatteru (trvá déle)
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
CACHE = KOREN / ".cache"
ZAKONY = KOREN / "zakony"
SMLOUVY = KOREN / "smlouvy"
EU = KOREN / "eu"

ELI = re.compile(r"eli/cz/(\w+)/(\d{4})/(\d+\w*)")
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


def nazev_souboru(citace: str) -> str:
    """Poslední záchrana, když se cesta nedá zjistit ze stavu. Pozor: z citace samotné se
    název souboru spolehlivě odvodit nedá — smlouva 4/2000 Sb. m. s. leží v
    `smlouvy/2000/4-2000-ms.md`, takže odkaz složený z čísla a roku by mířil na zákon."""
    m = re.match(r"(\d+\w*)/(\d{4})", citace)
    return f"{m.group(1)}-{m.group(2)}" if m else citace


def nacti_stav() -> dict:
    cesta = CACHE / "stav.json"
    return json.loads(cesta.read_text(encoding="utf-8")) if cesta.exists() else {}


def druh_predpisu(nazev: str) -> str:
    n = nazev.lower()
    for klic, popis in (
        ("zákon", "zákon"),
        ("nařízení vlády", "nařízení vlády"),
        ("vyhláška", "vyhláška"),
        ("sdělení", "sdělení"),
        ("nález", "nález"),
        ("usnesení", "usnesení"),
        ("opatření", "opatření"),
    ):
        if n.startswith(klic) or f" {klic}" in n[:40]:
            return popis
    return "ostatní"


def nacti_platnost() -> dict:
    """Co je zrušené, podle tools/platnost.py. Bez něj se rejstřík vygeneruje bez značek."""
    cesta = KOREN / ".zmeny" / "platnost.json"
    if not cesta.exists():
        return {}
    return json.loads(cesta.read_text(encoding="utf-8"))


def rejstrik(stav: dict) -> str:
    zruseno = nacti_platnost()
    po_letech: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for citace, udaje in stav.items():
        m = re.match(r"(\d+\w*)/(\d{4})", citace)
        if m:
            kdy = (zruseno.get(citace) or {}).get("k", "")
            # Cesta se bere ze stavu, ne skládá z citace — jinak by odkaz na smlouvu
            # 4/2000 Sb. m. s. mířil na zákon 4/2000 Sb., což je jiný předpis.
            soubor = udaje.get("soubor", "")
            stem = Path(soubor).stem if soubor else nazev_souboru(citace)
            po_letech[m.group(2)].append((citace, udaje.get("nazev", ""), kdy, stem))

    radky = [
        "---",
        "tags:",
        "  - rejstřík",
        "---",
        "",
        "# Rejstřík předpisů",
        "",
        f"Celkem {sum(len(v) for v in po_letech.values()):,} předpisů, "
        f"z toho {sum(1 for v in po_letech.values() for p in v if p[2]):,} zrušených — ty jsou "
        "označené ~~přeškrtnutím~~ a datem. **Nepracuj s nimi jako s platným právem.**",
        "",
        "Generuje `tools/rejstrik.py`, needituj ručně.",
        "",
        "Nejnovější změny jsou v [[CHANGELOG]].",
        "",
    ]
    for rok in sorted(po_letech, reverse=True):
        polozky = sorted(po_letech[rok], key=lambda p: int(re.match(r"(\d+)", p[0]).group(1)))
        radky.append(f"## {rok}")
        radky.append("")
        for citace, nazev, kdy, stem in polozky:
            if kdy:
                radky.append(f"- ~~[[{stem}|{citace}]]~~ {nazev} — *zrušeno {kdy}*".rstrip())
            else:
                radky.append(f"- [[{stem}|{citace}]] {nazev}".rstrip())
        radky.append("")
    return "\n".join(radky)


BASE = """filters:
  or:
    - 'file.hasTag("zakon")'
    - 'file.hasTag("smlouva")'
properties:
  citace:
    displayName: Citace
  nazev:
    displayName: Název
  ucinnost_od:
    displayName: Znění k
  rok:
    displayName: Rok
  zruseno_k:
    displayName: Zrušeno k
views:
  - type: table
    name: Platné předpisy
    filters:
      and:
        - '!file.hasTag("zruseno")'
    order:
      - citace
      - nazev
      - ucinnost_od
    sort:
      - property: rok
        direction: DESC
  - type: table
    name: Zrušené předpisy
    filters:
      and:
        - 'file.hasTag("zruseno")'
    order:
      - citace
      - nazev
      - zruseno_k
    sort:
      - property: zruseno_k
        direction: DESC
  - type: table
    name: Všechny předpisy
    order:
      - citace
      - nazev
      - ucinnost_od
      - zruseno_k
    sort:
      - property: rok
        direction: DESC
  - type: table
    name: Od roku 2020
    filters:
      and:
        - 'rok >= 2020'
        - '!file.hasTag("zruseno")'
    order:
      - citace
      - nazev
      - ucinnost_od
    sort:
      - property: rok
        direction: DESC
"""


def doplin_odkazy() -> int:
    """Do frontmatteru každého předpisu doplní, na které jiné předpisy odkazuje."""
    cesta = CACHE / "008.json.gz"
    if not cesta.exists():
        print("  008PravniAktOdkaz není stažený, odkazy přeskočeny")
        return 0

    with gzip.open(cesta, "rt", encoding="utf-8") as f:
        polozky = json.load(f).get("položky", [])

    odkazy: dict[str, set[str]] = defaultdict(set)
    for o in polozky:
        zdroj = ELI.search(str((o.get("znění-fragment") or {}).get("iri", "")))
        cil = ELI.search(str(o.get("odkaz-iri") or (o.get("právní-akt-cíl") or {}).get("iri", "")))
        if zdroj and cil and zdroj.groups() != cil.groups():
            odkazy[f"{zdroj.group(3)}/{zdroj.group(2)}"].add(f"{cil.group(3)}/{cil.group(2)}")

    upraveno = 0
    for soubor in list(ZAKONY.rglob("*.md")) + list(SMLOUVY.rglob("*.md")):
        klic = soubor.stem.replace("-", "/", 1)
        klic = f"{klic.split('/')[0]}/{soubor.parent.name}"
        cile = sorted(odkazy.get(klic, ()))[:40]
        if not cile:
            continue
        text = soubor.read_text(encoding="utf-8")
        if "\nodkazy:" in text:
            continue
        seznam = "\nodkazy:\n" + "\n".join(f'  - "[[{nazev_souboru(c + "/x")}]]"' for c in cile)
        text = FRONTMATTER.sub(lambda m: f"---\n{m.group(1)}{seznam}\n---\n", text, count=1)
        soubor.write_text(text, encoding="utf-8")
        upraveno += 1
    return upraveno


def rejstrik_eu() -> str:
    """Právo EU po letech. Vlastní soubor, protože 24 tisíc položek by český rejstřík zdvojnásobilo."""
    po_letech: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for soubor in sorted(EU.rglob("*.md")):
        text = soubor.read_text(encoding="utf-8")
        hlavicka = text.split("\n---\n", 1)[0]
        nazev = m.group(1) if (m := re.search(r'^nazev: "?(.+?)"?$', hlavicka, re.M)) else ""
        # Konec platnosti zapisuje platnost-eu.py; bez něj by tu 95/46/ES stála jako živé právo.
        kdy = m.group(1) if (m := re.search(r"^zruseno_k: (\S+)", hlavicka, re.M)) else ""
        po_letech[soubor.parent.name].append((soubor.stem, nazev, kdy))

    radky = ["---", "tags:", "  - rejstřík", "---", "", "# Rejstřík práva EU", "",
             "Nařízení a směrnice v češtině, "
             + f"{sum(len(v) for v in po_letech.values()):,}".replace(",", " ")
             + " předpisů. Soubor se jmenuje podle CELEXu: `32016R0679` je GDPR.", "",
             "Předpisy, které pozbyly platnosti, jsou ~~přeškrtnuté~~ s datem. "
             "**Nepracuj s nimi jako s platným právem.**", ""]
    for rok in sorted(po_letech, reverse=True):
        polozky = sorted(po_letech[rok])
        radky += [f"## {rok} ({len(polozky)})", ""]
        radky += [f"- ~~[[{celex}|{celex}]]~~ {nazev[:110]} — *pozbylo platnosti {kdy}*" if kdy
                  else f"- [[{celex}|{celex}]] {nazev[:110]}"
                  for celex, nazev, kdy in polozky]
        radky.append("")
    return "\n".join(radky)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--odkazy", action="store_true")
    args = p.parse_args()

    stav = nacti_stav()
    if not stav:
        print("stav.json je prázdný — nejdřív spusť tools/stahni.py")
        return 1

    (KOREN / "Rejstřík.md").write_text(rejstrik(stav), encoding="utf-8")
    if EU.exists():
        (KOREN / "Rejstřík EU.md").write_text(rejstrik_eu(), encoding="utf-8")
    (KOREN / "Předpisy.base").write_text(BASE, encoding="utf-8")
    print(f"Rejstřík.md: {len(stav):,} předpisů")
    print("Předpisy.base: tabulka pro Obsidian Bases")

    if args.odkazy:
        print(f"odkazy doplněny do {doplin_odkazy():,} souborů")
    return 0


if __name__ == "__main__":
    sys.exit(main())
