"""Postaví fulltextový index nad sbírkou — po paragrafech, ne po souborech.

Korpus má přes půl gigabajtu a občanský zákoník sám 1,4 MB. Index po souborech by na dotaz vrátil
celý zákon, což je pro agenta dražší než práce, kvůli které ho otevírá. Proto je jednotkou indexu
**paragraf**: zásah vrátí ustanovení, ne kodex.

Staví se SQLite s FTS5 a `remove_diacritics 2`, takže „najemni" najde „nájemní" — u českého textu
to není kosmetika, ale rozdíl mezi nalezeno a nenalezeno. Embeddingy tu záměrně nejsou: `§ 2235`
je přesný řetězec, ne významový odstín, a na právní text dává lexikální shoda přesnější výsledky.

**Stemming se zkoušel a zhoršil to.** Pravidlový český stemmer (koncovky pádů, bez slovníku)
přidaný jako další indexovaný sloupec srazil trefu na první místo z 16/28 na 12/28 a na správný
předpis z 24/28 na 22/28, přitom index nafoukl z 1,2 na 2,0 GB a stavbu z 3 na 12 minut. Kmen je
pro právní text příliš hrubý: „nájem" se ořízne na „naj" a bm25 nad kmeny má menší rozlišovací
sílu. Skloňování se proto řeší až v mcp.py, a jen když přesný dotaz nenajde nic.

Index je odvozená věc, do gitu nepatří — postav si ho po stažení sbírky:

    python3 tools/index.py           # postaví .cache/index.db
    python3 tools/index.py --dotaz "nájem bytu"   # zkušební dotaz
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
ZAKONY = KOREN / "zakony"
SMLOUVY = KOREN / "smlouvy"
EU = KOREN / "eu"
METADATA = KOREN / "judikatura" / "metadata"
DB = KOREN / ".cache" / "index.db"

# "z. č. 89/2012 Sb.", "vyhl. č. 177/1996 Sb." — kterých předpisů se rozhodnutí dovolává
CITOVANY = re.compile(r"(?:z|zák|vyhl|nař\.?\s*vl|nař|opatř|sděl)\.\s*č\.\s*(\d+/\d{4})\s*Sb")

# "##### § 2235", "## čl. 1.", "## Článek 12" — úroveň nadpisu se liší podle hloubky struktury,
# takže se na ni nedá vázat, a označení má v korpusu několik pravopisů: Čl./čl./Článek/ČLÁNEK,
# s tečkou za číslem i bez. Původní regex uměl jen "§" a "Čl.", čímž vypadlo 16 236 předpisů —
# skoro polovina, včetně souborů přes 10 MB. Strukturní nadpisy (ČÁST, HLAVA, Díl) sem nepatří.
USEK = re.compile(
    r"^(#{1,6}) (§+\s?[\d\w/-]+|(?:Čl|čl|ČL)\.\s?[\d\w/-]+|(?:Článek|článek|ČLÁNEK)\s[\d\w/-]+"
    r"|(?:Jediný|jediný|JEDINÝ)\s+(?:článek|ČLÁNEK))\.?\s*$",
    re.M,
)
NADPIS = re.compile(r"^\*\*(.+)\*\*$", re.M)


def rozdel_frontmatter(text: str) -> tuple[dict[str, str], str, int]:
    """Frontmatter jako slovník, tělo a číslo řádku, na kterém tělo začíná."""
    if not text.startswith("---\n"):
        return {}, text, 1
    konec = text.find("\n---\n", 4)
    if konec == -1:
        return {}, text, 1

    meta: dict[str, str] = {}
    for radek in text[4:konec].split("\n"):
        if ":" in radek and not radek.startswith(" "):
            k, _, v = radek.partition(":")
            meta[k.strip()] = v.strip().strip('"')
    telo = text[konec + 5:]
    return meta, telo, text[:konec + 5].count("\n") + 1


def useky(telo: str, posun: int):
    """Jednotlivé paragrafy: označení, nadpis, text, řádek."""
    nalezy = list(USEK.finditer(telo))
    if not nalezy:
        # Předpis bez členění na paragrafy — část nařízení EU nese celý obsah v odůvodnění.
        # Bez tohohle by z indexu vypadl celý, včetně dokumentů přes 50 kB.
        cely = "\n".join(r for r in telo.split("\n") if not r.startswith("#")).strip()
        if cely:
            yield "(bez členění)", "", cely, posun
        return

    for i, m in enumerate(nalezy):
        konec = nalezy[i + 1].start() if i + 1 < len(nalezy) else len(telo)
        blok = telo[m.end():konec]

        # Za označením může stát nadpis paragrafu (**Základní ustanovení**) — patří k němu,
        # ale do textu ne, aby se dal zobrazit zvlášť.
        nadpis = ""
        prvni = NADPIS.match(blok.lstrip("\n"))
        if prvni:
            nadpis = prvni.group(1)
            blok = blok.lstrip("\n")[prvni.end():]

        # Strukturní nadpisy (ČÁST, HLAVA) uvnitř bloku do textu paragrafu nepatří.
        cisty = "\n".join(r for r in blok.split("\n") if not r.startswith("#")).strip()
        yield m.group(2), nadpis, cisty, posun + telo[:m.start()].count("\n")


# Lemmatizace je volitelná: bez `simplemma` se index postaví jako dřív a hledání jede na
# tvarech slov, takže dotaz musí mít hvězdičku. Se `simplemma` přibude sloupec se základními
# tvary, díky kterému „bytu“ najde „byt“. Je to čistý Python, takže `pip install simplemma`
# projde na Windows i na Macu bez kompilátoru.
try:
    import simplemma

    _LEMMA_CACHE: dict[str, str] = {}
except ImportError:
    simplemma = None


_SLOVO = re.compile(r"\w+", re.UNICODE)


def lemmatizuj(text: str) -> str:
    """Základní tvary slov textu, oddělené mezerou. Bez simplemma vrací prázdno.

    Cache je tu proto, že korpus má přes 90 milionů slov, ale jen jednotky milionů různých —
    bez ní by lemmatizace běžela hodiny místo minut."""
    if simplemma is None:
        return ""
    zaklady = []
    for m in _SLOVO.finditer(text.lower()):
        s = m.group(0)
        if (z := _LEMMA_CACHE.get(s)) is None:
            try:
                z = simplemma.lemmatize(s, lang="cs")
            except Exception:  # noqa: BLE001 — na neznámém tvaru se nepadá, nechá se jak je
                z = s
            _LEMMA_CACHE[s] = z
        if z != s:
            zaklady.append(z)
    return " ".join(zaklady)


def autorita() -> dict[str, float]:
    """Jak je předpis v praxi důležitý, měřeno tím, kolik rozhodnutí se ho dovolává.

    Bez tohohle signálu vyhrávají nad kodexy okrajové předpisy: bm25 dělí skóre délkou textu,
    takže slovo v krátké vyhlášce váží víc než v rozsáhlém zákoníku. Na dotaz po výpovědi
    z nájmu pak přijde nařízení z roku 2021 a ne občanský zákoník. Měřeno na zlaté sadě
    (tools/dotazy.py) tenhle signál zvedl trefu na správný předpis ze 75 % na 89 %.

    Logaritmus proto, že rozdíl mezi stem a tisícem citací má vážit víc než mezi sto tisíci
    a sto tisíci jedna."""
    if not METADATA.exists():
        return {}
    pocty: collections.Counter[str] = collections.Counter()
    for soubor in sorted(METADATA.glob("*.jsonl.gz")):
        with gzip.open(soubor, "rt", encoding="utf-8") as f:
            for radek in f:
                videno = set()
                for u in json.loads(radek).get("zminenaUstanoveni") or []:
                    m = CITOVANY.search(u)
                    if m:
                        videno.add(f"{m.group(1)} Sb.")
                pocty.update(videno)
    vysledek = {citace: math.log(1 + n) for citace, n in pocty.items()}
    vysledek.update(autorita_eu())
    return vysledek


# „(EU) 2016/679“, „(ES) č. 1215/2012“, „2011/83/EU“ — tři tvary, kterými se právo EU cituje.
_CITACE_EU = (
    re.compile(r"\((?:EU|ES|EHS|Euratom)\)\s*(?:č\.\s*)?(\d{4})/(\d{1,4})\b"),
    re.compile(r"\((?:EU|ES|EHS|Euratom)\)\s*(?:č\.\s*)?(\d{1,4})/(\d{4})\b"),
    re.compile(r"\b(\d{4})/(\d{1,4})/(?:EU|ES|EHS|Euratom)\b"),
)


def autorita_eu() -> dict[str, float]:
    """Totéž pro právo EU, jen se nepočítají rozhodnutí soudů, ale odkazy z jiných předpisů.

    Judikatura tenhle signál dát nemůže: otevřená data justice.cz citují české předpisy, ne
    CELEXy, takže nařízení i směrnice mají z judikatury nulu a v řazení prohrávají s českým
    zákonem, který má stejná slova. Změřeno na zlaté sadě: bez tohohle nenajde hledání ani
    jeden ze čtyř dotazů na právo EU."""
    znama: dict[tuple[str, str], str] = {}
    for cesta in (KOREN / "eu").rglob("*.md"):
        if (m := re.match(r"3(\d{4})([RL])(\d{4})$", cesta.stem)):
            znama[(m.group(1), str(int(m.group(3))))] = cesta.stem

    pocty: collections.Counter[str] = collections.Counter()
    for cesta in list(KOREN.rglob("*.md")):
        if any(c in cesta.parts for c in (".git", "judikatura")):
            continue
        try:
            text = cesta.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        videno = set()
        for vzor in _CITACE_EU:
            for m in vzor.finditer(text):
                a, b = m.group(1), m.group(2)
                rok, cislo = (a, b) if len(a) == 4 and a.startswith(("19", "20")) else (b, a)
                if (celex := znama.get((rok, str(int(cislo))))) and celex != cesta.stem:
                    videno.add(celex)
        pocty.update(videno)
    return {celex: math.log(1 + n) for celex, n in pocty.items()}


def postav(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()

    spoj = sqlite3.connect(db)
    spoj.executescript("""
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE stavba (zahajeno REAL);
        CREATE TABLE predpis (
            citace TEXT PRIMARY KEY, rok INTEGER, cislo TEXT, nazev TEXT, druh TEXT,
            eli TEXT, ucinnost_od TEXT, zruseno_k TEXT, zrusil TEXT, soubor TEXT,
            uplne_zneni INTEGER DEFAULT 0, autorita REAL DEFAULT 0, pristi_zneni_od TEXT
        );
        CREATE TABLE usek (
            id INTEGER PRIMARY KEY, predpis TEXT, oznaceni TEXT, nadpis TEXT,
            text TEXT, radek INTEGER, predpis_nazev TEXT, lemma TEXT
        );
        CREATE INDEX usek_predpis ON usek (predpis, oznaceni);
        -- Název předpisu je součástí každého ustanovení schválně: § 16 zákona o daních z příjmů
        -- zní jen „Sazba daně činí…" a slovo „příjem" v sobě nemá, takže by ho dotaz na daň
        -- z příjmů minul. Ustanovení se hledá v kontextu zákona, ve kterém stojí.
        CREATE VIRTUAL TABLE usek_fts USING fts5(
            oznaceni, nadpis, text, predpis_nazev, lemma,
            content='usek', content_rowid='id',
            tokenize="unicode61 remove_diacritics 2"
        );
    """)

    zacatek = time.time()
    # Stavba trvá minuty a soubory se během ní můžou měnit; čas začátku je jediný bezpečný
    # bod, podle kterého se pozná, že je index proti předpisům pozadu.
    spoj.execute("INSERT INTO stavba (zahajeno) VALUES (?)", (zacatek,))
    print("  počítám, jak často soudy který předpis citují …", flush=True)
    vahy = autorita()
    predpisu = usekov = 0
    for cesta in sorted(ZAKONY.rglob("*.md")) + sorted(SMLOUVY.rglob("*.md")) + sorted(EU.rglob("*.md")):
        meta, telo, posun = rozdel_frontmatter(cesta.read_text(encoding="utf-8"))
        # předpisy EU nemají citaci ve smyslu Sbírky, identifikuje je CELEX
        citace = meta.get("citace") or meta.get("celex") or cesta.stem
        nazev = meta.get("nazev", "")
        spoj.execute(
            "INSERT OR REPLACE INTO predpis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (citace, int(meta.get("rok") or 0), meta.get("cislo", ""), nazev,
             meta.get("druh", ""), meta.get("eli", ""), meta.get("ucinnost_od", ""),
             meta.get("zruseno_k", ""), meta.get("zrusil", ""), str(cesta.relative_to(KOREN)),
             int("plné znění" in nazev), vahy.get(citace, 0.0), meta.get("pristi_zneni_od", "")),
        )
        predpisu += 1
        davka = [(citace, o, n, t, r, nazev, lemmatizuj(t)) for o, n, t, r in useky(telo, posun) if t]
        if davka:
            spoj.executemany(
                "INSERT INTO usek (predpis, oznaceni, nadpis, text, radek, predpis_nazev, lemma) "
                "VALUES (?,?,?,?,?,?,?)",
                davka)
            usekov += len(davka)
        if predpisu % 2000 == 0:
            spoj.commit()
            print(f"  {predpisu:,} předpisů, {usekov:,} ustanovení", flush=True)

    spoj.commit()
    print("  plním fulltext …", flush=True)
    spoj.execute("INSERT INTO usek_fts (rowid, oznaceni, nadpis, text, predpis_nazev, lemma) "
                 "SELECT id, oznaceni, nadpis, text, predpis_nazev, lemma FROM usek")
    spoj.execute("INSERT INTO usek_fts (usek_fts) VALUES ('optimize')")
    spoj.commit()
    spoj.close()

    print(f"hotovo za {time.time() - zacatek:.0f} s: {predpisu:,} předpisů, {usekov:,} ustanovení")
    print(f"index: {db} ({db.stat().st_size / 1024 / 1024:.0f} MB)")


def zkus(db: Path, dotaz: str) -> None:
    spoj = sqlite3.connect(db)
    spoj.row_factory = sqlite3.Row
    radky = spoj.execute("""
        SELECT p.citace, p.nazev, u.oznaceni, u.nadpis, p.zruseno_k,
               snippet(usek_fts, 2, '«', '»', ' … ', 18) AS uryvek, bm25(usek_fts) AS skore
        FROM usek_fts JOIN usek u ON u.id = usek_fts.rowid
        JOIN predpis p ON p.citace = u.predpis
        WHERE usek_fts MATCH ? ORDER BY skore LIMIT 8
    """, (dotaz,)).fetchall()
    for r in radky:
        stav = f" [zrušeno {r['zruseno_k']}]" if r["zruseno_k"] else ""
        print(f"\n{r['citace']} {r['oznaceni']}{stav} — {r['nazev'][:60]}")
        if r["nadpis"]:
            print(f"  {r['nadpis']}")
        print(f"  {r['uryvek'][:220]}")
    spoj.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dotaz", help="jen vyzkoušet dotaz nad hotovým indexem")
    args = p.parse_args()

    if args.dotaz:
        if not DB.exists():
            print("index neexistuje, spusť nejdřív `python3 tools/index.py`", file=sys.stderr)
            return 1
        zkus(DB, args.dotaz)
        return 0

    # Bez FTS5 by stavba spadla až po načtení předpisů, s tracebackem místo rady.
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError:
        print(f"SQLite v tomhle Pythonu ({sys.executable}) nemá FTS5, index nepostaví. Použij jiný "
              f"Python, například z python.org nebo `uv python install 3.12`, nebo stáhni hotový "
              f"index z Releases.", file=sys.stderr)
        return 1

    postav(DB)
    return 0


if __name__ == "__main__":
    sys.exit(main())
