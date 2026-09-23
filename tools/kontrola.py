"""Projde repozitář a hlásí, co v něm nesedí.

Obě vážné chyby, které tenhle balíček zatím potkaly, se našly náhodou — kolize řad při čtení logu
a děravý index při zběžném skenu. Kontrola je tu proto, aby se příště našly samy: pouští se po
každé aktualizaci, vrací nenulový kód, když něco nesedí, a hodí se do CI.

Kontroluje se to, co se už jednou rozbilo, a to, co se rozbít může tiše:

* integrita — citace sedí se souborem i řadou, nic se nepřepisuje, stav odpovídá disku
* struktura — frontmatter, nadpis, text, který nekončí v půlce
* platnost — zrušený předpis je označený všude, neplatný nikde
* index — pokrývá všechny soubory a každý předpis má ustanovení
* odkazy — wikilinky vedou na existující cíl

    python3 tools/kontrola.py              # shrnutí, pár příkladů u každého nálezu
    python3 tools/kontrola.py --podrobne   # vypíše všechny nálezy
    python3 tools/kontrola.py --jen index  # jen vybranou skupinu
"""

from __future__ import annotations

import argparse
import json
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
ZAKONY = KOREN / "zakony"
SMLOUVY = KOREN / "smlouvy"
EU = KOREN / "eu"
JUDIKATURA = KOREN / "judikatura"
DB = KOREN / ".cache" / "index.db"
STAV = KOREN / ".cache" / "stav.json"
PLATNOST = KOREN / ".zmeny" / "platnost.json"

CITACE = re.compile(r"^citace: (.+)$", re.M)
ZRUSENO_K = re.compile(r"^zruseno_k: (.+)$", re.M)
USEK = re.compile(r"^#{1,6} (§+\s?[\d\w/-]+|(?:Čl|čl|ČL)\.\s?[\d\w/-]+|(?:Článek|článek|ČLÁNEK)\s[\d\w/-]+)\.?\s*$", re.M)
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
KOTVA = re.compile(r"\[\[([^\]|#]+)#([^\]|]+)")
# [[4-2000-ms|4/2000 Sb. m. s.]] — popisek musí sedět s citací v cílovém souboru
POPISEK = re.compile(r"\[\[([^\]|#]+)\|([^\]]+)\]\]")
NADPIS_RADEK = re.compile(r"^#{1,6} (.+?)\s*$", re.M)

# Kratší tělo pod nadpisem než tohle znamená, že se stáhla hlavička bez textu. Měřit velikost
# celého souboru nešlo: nejkratší předpisy jsou legitimní třířádková oznámení, kde je text celý.
MALE_TELO = 60


class Nalezy:
    def __init__(self) -> None:
        self.skupiny: dict[str, list[str]] = defaultdict(list)

    def pridej(self, skupina: str, zprava: str) -> None:
        self.skupiny[skupina].append(zprava)

    def pocet(self) -> int:
        return sum(len(v) for v in self.skupiny.values())


def soubory() -> list[Path]:
    """České předpisy — ty se identifikují citací a mají značky zrušení."""
    return sorted(ZAKONY.rglob("*.md")) + sorted(SMLOUVY.rglob("*.md"))


def soubory_eu() -> list[Path]:
    """Předpisy EU. Mají vlastní hlavičku (CELEX místo citace) a zrušení se u nich nesleduje,
    takže se kontrolují zvlášť."""
    return sorted(EU.rglob("*.md"))


def citace_souboru(text: str) -> str:
    m = CITACE.search(text)
    return m.group(1).strip().strip('"') if m else ""


def kontrola_integrity(n: Nalezy) -> None:
    """Citace musí sedět s názvem souboru i s řadou podle složky, a žádná se nesmí opakovat.
    Tady se projevila kolize řad: smlouva 1/2000 přepsala zákon 1/2000, protože cesta stála
    jen na čísle a roce."""
    videne: dict[str, Path] = {}
    for cesta in soubory():
        text = cesta.read_text(encoding="utf-8")
        citace = citace_souboru(text)
        rel = cesta.relative_to(KOREN)

        if not citace:
            n.pridej("integrita", f"{rel}: chybí citace ve frontmatteru")
            continue

        if citace in videne:
            n.pridej("integrita", f"{rel}: citace {citace} už je v {videne[citace].relative_to(KOREN)}")
        videne[citace] = cesta

        je_smlouva = cesta.stem.endswith("-ms")
        ma_byt_smlouva = "m. s." in citace
        if je_smlouva != ma_byt_smlouva:
            kde = "smlouvy/" if je_smlouva else "zakony/"
            n.pridej("integrita", f"{rel}: {citace} leží v {kde}, tam nepatří")

        cislo, rok = cesta.stem.removesuffix("-ms").rsplit("-", 1)
        if not citace.startswith(f"{cislo}/{rok} "):
            n.pridej("integrita", f"{rel}: název souboru neodpovídá citaci {citace}")
        if cesta.parent.name != rok:
            n.pridej("integrita", f"{rel}: leží ve složce {cesta.parent.name}, ale je z roku {rok}")

    if STAV.exists():
        stav = json.loads(STAV.read_text(encoding="utf-8"))
        if len(stav) != len(videne):
            n.pridej("integrita", f"stav.json má {len(stav):,} záznamů, na disku je {len(videne):,} souborů")


def kontrola_struktury(n: Nalezy) -> None:
    """Soubor musí mít frontmatter, nadpis a text. Prázdný nebo useknutý předpis vypadá jako
    platný, dokud se na něj někdo nepodívá."""
    for cesta in soubory():
        text = cesta.read_text(encoding="utf-8")
        rel = cesta.relative_to(KOREN)

        if not text.startswith("---\n") or text.find("\n---\n", 4) == -1:
            n.pridej("struktura", f"{rel}: rozbitý frontmatter")
            continue
        telo = text[text.find("\n---\n", 4) + 5:]
        nadpis = re.search(r"^# .*$", telo, re.M)
        if not nadpis:
            n.pridej("struktura", f"{rel}: chybí nadpis předpisu")
            continue

        obsah = re.sub(r"^[>#*\s]*$", "", telo[nadpis.end():], flags=re.M).strip()
        if len(obsah) < MALE_TELO:
            n.pridej("struktura", f"{rel}: pod nadpisem jen {len(obsah)} znaků, nejspíš hlavička bez textu")


def kontrola_platnosti(n: Nalezy) -> None:
    """Zrušený předpis musí být označený ve frontmatteru i nad textem, a neplatný nesmí být
    označený nikde. Značka na jednom místě nestačí — agent přijde i grepem."""
    if not PLATNOST.exists():
        n.pridej("platnost", "chybí .zmeny/platnost.json — spusť tools/platnost.py")
        return
    zruseno = json.loads(PLATNOST.read_text(encoding="utf-8"))

    for cesta in soubory():
        text = cesta.read_text(encoding="utf-8")
        rel = cesta.relative_to(KOREN)
        citace = citace_souboru(text)
        ma_byt = citace in zruseno

        ma_klic = bool(ZRUSENO_K.search(text))
        ma_tag = "\n  - zruseno\n" in text
        ma_callout = "> [!danger]" in text

        if ma_byt and not (ma_klic and ma_tag and ma_callout):
            chybi = [p for p, je in (("zruseno_k", ma_klic), ("tag", ma_tag), ("callout", ma_callout)) if not je]
            n.pridej("platnost", f"{rel}: zrušený předpis, chybí {', '.join(chybi)}")
        if not ma_byt and (ma_klic or ma_tag or ma_callout):
            n.pridej("platnost", f"{rel}: není v platnost.json, ale nese značku zrušení")


def kontrola_indexu(n: Nalezy) -> None:
    """Index musí pokrývat všechny soubory a každý předpis musí mít aspoň jedno ustanovení.
    Právě tahle kontrola by odhalila, že regex neuměl `čl.` a vypadlo 16 236 předpisů."""
    if not DB.exists():
        n.pridej("index", "index neexistuje — spusť tools/index.py")
        return

    spoj = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    v_indexu = {r[0] for r in spoj.execute("SELECT soubor FROM predpis")}
    na_disku = {str(c.relative_to(KOREN)) for c in soubory() + soubory_eu()}

    for chybi in sorted(na_disku - v_indexu)[:200]:
        n.pridej("index", f"{chybi}: na disku, ale ne v indexu")
    for navic in sorted(v_indexu - na_disku)[:200]:
        n.pridej("index", f"{navic}: v indexu, ale ne na disku")

    # Předpis bez ustanovení je podezřelý, ale ne vždy chybný — sdělení a opatření
    # opravdu paragrafy nemají. Hlásí se, až když text vypadá, že je v něm co indexovat.
    prazdne = spoj.execute("""
        SELECT p.soubor FROM predpis p
        WHERE NOT EXISTS (SELECT 1 FROM usek u WHERE u.predpis = p.citace)
    """).fetchall()
    for (soubor,) in prazdne:
        cesta = KOREN / soubor
        if not cesta.exists():
            continue
        text = cesta.read_text(encoding="utf-8")
        if USEK.search(text):
            n.pridej("index", f"{soubor}: má označená ustanovení, ale v indexu žádné nejsou")
    spoj.close()


def kontrola_stazeni(n: Nalezy) -> None:
    """Předpisy, které se při posledním běhu nepodařilo stáhnout, zůstaly ve starém znění.

    e-Sbírka některá XML trvale odmítá vydat — v září 2026 i u občanského zákoníku a zákoníku
    práce. Bez tohohle se ta informace ztratí v logu a nikdo nepozná, že kodex zastarává."""
    soubor = KOREN / ".cache" / "nestazene.json"
    if not soubor.exists():
        return
    try:
        nestazene = json.loads(soubor.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for citace, duvod in sorted(nestazene.items()):
        n.pridej("stahovani", f"{citace} se nepodařilo stáhnout ({duvod[:60]}) — zůstává starý")


def kontrola_zneni(n: Nalezy) -> None:
    """Ohlášené budoucí znění musí být i nad textem a musí být opravdu pozdější než to uložené.

    Callout mazal `platnost.py` spolu se svým varováním; grepem vytažený paragraf by pak
    o chystané změně mlčel."""
    for cesta in soubory():
        hlava = cesta.read_text(encoding="utf-8")[:3000]
        if not (m := re.search(r"^pristi_zneni_od: (\S+)", hlava, re.M)):
            continue
        rel = cesta.relative_to(KOREN)
        if "> [!warning] Chystá se nové znění" not in hlava:
            n.pridej("zneni", f"{rel}: ohlášené znění od {m.group(1)} chybí nad textem")
        ucinnost = re.search(r"^ucinnost_od: (\S+)", hlava, re.M)
        if ucinnost and ucinnost.group(1) >= m.group(1):
            n.pridej("zneni", f"{rel}: budoucí znění {m.group(1)} není pozdější než {ucinnost.group(1)}")


def kontrola_eu(n: Nalezy) -> None:
    """Předpisy EU: hlavička s CELEXem, který sedí s názvem souboru, a aspoň jeden článek.
    Prázdný soubor by znamenal, že se stáhla obálka bez textu."""
    videny: dict[str, Path] = {}
    for cesta in soubory_eu():
        text = cesta.read_text(encoding="utf-8")
        rel = cesta.relative_to(KOREN)

        m = re.search(r"^celex: (.+)$", text, re.M)
        if not m:
            n.pridej("eu", f"{rel}: chybí CELEX ve frontmatteru")
            continue
        celex = m.group(1).strip()

        if celex != cesta.stem:
            n.pridej("eu", f"{rel}: CELEX {celex} neodpovídá názvu souboru")
        if celex in videny:
            n.pridej("eu", f"{rel}: CELEX {celex} už je v {videny[celex].relative_to(KOREN)}")
        videny[celex] = cesta

        # Corrigendum („Oprava nařízení…“, značka R(NN)) je z povahy pár řádek oprav sazby
        # a článek nemá. Stejně tak část nařízení nese celý obsah v odůvodnění. Hlásí se proto
        # jen soubor bez jakéhokoli textu — to je ta obálka bez obsahu.
        telo = text.split("---", 2)[-1]
        obsah = "\n".join(r for r in telo.split("\n") if not r.startswith("#")).strip()
        if not obsah:
            n.pridej("eu", f"{rel}: prázdný — stáhla se obálka bez textu")


def kontrola_odkazu(n: Nalezy) -> None:
    """Wikilink, který nikam nevede, je v Obsidianu tichá chyba — v náhledu vypadá jako odkaz.
    Kontroluje se i kotva za mřížkou: `[[89-2012#§ 2235]]` musí mít v cílovém souboru nadpis,
    který se přesně takhle jmenuje."""
    # Cílem odkazu může být i předpis EU; bez nich by `[[32016R0679#Článek 6]]` vypadal rozbitě.
    podle_stem = {c.stem: c for c in soubory() + soubory_eu()}
    cile = set(podle_stem) | {"CHANGELOG", "Rejstřík", "README", "Průvodce", "CLAUDE"}
    kotvy: dict[str, set[str]] = {}
    # CHANGELOG má přes 39 000 wikilinků a dřív ho nikdo nekontroloval; tři z nich mířily
    # na složku místo na soubor, což je v Obsidianu tichá chyba.
    zdroje = [KOREN / "Rejstřík.md", KOREN / "CHANGELOG.md", KOREN / "README.md",
              KOREN / "Průvodce.md", KOREN / "CLAUDE.md"] + sorted(JUDIKATURA.rglob("*.md"))

    for cesta in zdroje:
        if not cesta.exists():
            continue
        rel = cesta.relative_to(KOREN)
        text = cesta.read_text(encoding="utf-8")

        for m in WIKILINK.finditer(text):
            cil = m.group(1).strip()
            if cil not in cile:
                n.pridej("odkazy", f"{rel}: odkaz [[{cil}]] nikam nevede")

        # Odkaz může mířit na existující soubor, a přesto být špatně: [[4-2000]] místo
        # [[4-2000-ms]] vede na zákon, ne na smlouvu téhož čísla. Popisek to prozradí.
        for m in POPISEK.finditer(text):
            cil, popisek = m.group(1).strip(), m.group(2).strip()
            if cil not in podle_stem or "#" in cil:
                continue
            # Popisek bývá i běžné slovo („… v platném znění [[10-2006|předpisu]] není"),
            # porovnávat se dá jen tam, kde se tváří jako citace.
            if not re.match(r"^\d+/\d{4}\s+Sb", popisek):
                continue
            skutecna = citace_souboru(podle_stem[cil].read_text(encoding="utf-8"))
            if skutecna and popisek.rstrip(".") != skutecna.rstrip("."):
                n.pridej("odkazy", f"{rel}: [[{cil}|{popisek}]] míří na {skutecna}")

        for m in KOTVA.finditer(text):
            cil, kotva = m.group(1).strip(), m.group(2).strip()
            if cil not in podle_stem:
                continue
            if cil not in kotvy:
                kotvy[cil] = set(NADPIS_RADEK.findall(podle_stem[cil].read_text(encoding="utf-8")))
            if kotva not in kotvy[cil]:
                n.pridej("odkazy", f"{rel}: kotva [[{cil}#{kotva}]] v cílovém souboru není")


KONTROLY = {
    "eu": kontrola_eu,
    "stahovani": kontrola_stazeni,
    "zneni": kontrola_zneni,
    "integrita": kontrola_integrity,
    "struktura": kontrola_struktury,
    "platnost": kontrola_platnosti,
    "index": kontrola_indexu,
    "odkazy": kontrola_odkazu,
}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--podrobne", action="store_true", help="vypsat všechny nálezy, ne jen ukázku")
    p.add_argument("--jen", choices=sorted(KONTROLY), action="append", help="jen vybranou skupinu")
    args = p.parse_args()

    n = Nalezy()
    for jmeno in args.jen or sorted(KONTROLY):
        print(f"kontroluji {jmeno} …", flush=True)
        KONTROLY[jmeno](n)

    # Nestažený předpis je vada zdroje, ne dat: soubor zůstal v posledním dobrém znění.
    # Kdyby shodil kontrolu, jeden předpis, který e-Sbírka trvale odmítá vydat, by týdenní
    # aktualizaci zablokoval natrvalo — stav se neuloží, a tak se o něj pokusí každý týden znovu.
    varovani = n.skupiny.pop("stahovani", [])
    if varovani:
        print(f"\n## varování: nestaženo {len(varovani)} předpisů, zůstávají v předchozím znění")
        for z in varovani[:8]:
            print(f"  {z}")

    if not n.pocet():
        print("\nvšechno sedí")
        return 0

    print(f"\nnálezů: {n.pocet():,}")
    for skupina in sorted(n.skupiny):
        zpravy = n.skupiny[skupina]
        print(f"\n## {skupina} ({len(zpravy):,})")
        for z in zpravy if args.podrobne else zpravy[:8]:
            print(f"  {z}")
        if not args.podrobne and len(zpravy) > 8:
            print(f"  … a dalších {len(zpravy) - 8:,} (--podrobne je vypíše)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
