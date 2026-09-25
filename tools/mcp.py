"""MCP server nad sbírkou — hledá po paragrafech, vrací ustanovení místo kodexů.

Agent, který si otevře `zakony/2012/89-2012.md`, spolyká 1,4 MB na jeden dotaz. Tenhle server mu
dá ten jeden paragraf. Jede nad indexem z `tools/index.py` (SQLite FTS5), nepotřebuje běžící
Obsidian ani nic nainstalovaného — jen Python.

    python3 tools/index.py      # nejdřív index
    python3 tools/mcp.py        # server mluví JSON-RPC po stdio

Zapojení do Claude Code (`.mcp.json`):

    {"mcpServers": {"zakony": {"command": "python3", "args": ["tools/mcp.py"]}}}

Dotaz je syntaxe FTS5: slova se AND-ují, `"přesná fráze"` v uvozovkách, `NEAR(a b, 10)` na
blízkost. Diakritika se ignoruje, takže „najem" najde „nájem".
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

# Systémový python3 na Macu je 3.9; starší by spadl až uprostřed volání nástroje.
if sys.version_info < (3, 9):
    sys.exit(f"czech-law-md potřebuje Python 3.9 nebo novější, tenhle je {sys.version.split()[0]}.")

KOREN = Path(__file__).resolve().parent.parent

# Fulltext stojí na FTS5 v SQLite, který Python přibaluje. Ne každý ho má — samostatné buildy
# Pythonu z roku 2025 ho vynechávaly — a bez vysvětlení to agent čte jako chybu v dotazu.
BEZ_FTS5 = ("Hledání nefunguje: SQLite v Pythonu, kterým server běží ({verze}), nemá modul FTS5. "
            "`paragraf`, `predpis` a `judikatura` fungují dál. Pro hledání spusť server jiným "
            "Pythonem, například z python.org nebo `uv python install 3.12`.")
DB = KOREN / ".cache" / "index.db"
JUDIKATURA = KOREN / "judikatura"
ZMENY = KOREN / ".zmeny" / "posledni.json"

VERZE = "2025-06-18"
# Verze serveru, ne protokolu. Drž ji shodnou s git tagem; rozhraní nástrojů je v betě.
VERZE_SERVERU = "0.1.0"

# Nejdelší ustanovení má přes sedm milionů znaků (§ 3 zákona 1/1998 Sb., celní sazebník),
# tedy zhruba 1,7 milionu tokenů na jediný dotaz. Server má agentovi kontext šetřit, ne ho
# zahltit, takže se text ořízne a řekne se, kde je zbytek.
NEJVIC_ZNAKU = 40_000


def orizni(text: str, kde: str) -> str:
    if len(text) <= NEJVIC_ZNAKU:
        return text
    return (text[:NEJVIC_ZNAKU]
            + f"\n\n… **Zkráceno**: ustanovení má {len(text):,} znaků, vypsáno prvních "
              f"{NEJVIC_ZNAKU:,}. Celé je v `{kde}`.".replace(",", " "))

NASTROJE = [
    {
        "name": "hledej",
        "description": (
            "Fulltextově prohledá právo platné v Česku a vrátí jednotlivá ustanovení, ne celé "
            "zákony. Zahrnuje Sbírku zákonů, mezinárodní smlouvy i nařízení a směrnice EU "
            "v češtině — předpis EU se cituje CELEXem (32016R0679 je GDPR) a člení na "
            "články, ne paragrafy. Diakritika se ignoruje. Syntaxe FTS5: slova se AND-ují, \"přesná fráze\" "
            "v uvozovkách, NEAR(a b, 10) na blízkost, OR na alternativu.\n\n"
            "POZOR: hledají se tvary slov, ne kmeny — čeština se neskloňuje automaticky. "
            "„bytu\" nenajde „byt\" ani „bytě\", „vypoved\" nenajde „vypovědět\". "
            "U ohebných slov proto piš hvězdičku nebo alternativy: "
            "„najm* OR najem*\", „byt*\", „vypove*\". Když dotaz nic nevrátí, "
            "zkrať slova na kmen a zopakuj.\n\n"
            "Ve výchozím stavu vrací jen živé právo: zrušené předpisy a historická „úplná znění\" "
            "(publikace textu jiného zákona) jsou vynechané, dokud nezapneš i_historicke."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dotaz": {"type": "string", "description": "hledaný výraz, např. 'nájem bytu výpověď'"},
                "limit": {"type": "integer", "description": "kolik ustanovení vrátit (výchozí 8)"},
                "predpis": {"type": "string", "description": "omezit na jeden předpis, např. '89/2012 Sb.' nebo '32016R0679'"},
                "i_historicke": {"type": "boolean", "description": "hledat i ve zrušených předpisech a v historických úplných zněních (výchozí ne)"},
            },
            "required": ["dotaz"],
        },
    },
    {
        "name": "paragraf",
        "description": (
            "Vrátí plné znění jednoho ustanovení podle předpisu a označení — `§ 2235` u českého "
            "předpisu, `Článek 6` u předpisu EU (stačí i holé číslo, doplní se podle druhu "
            "předpisu). Parametrem `okoli` "
            "přibere i sousední ustanovení — právní institut bývá rozepsaný přes několik "
            "paragrafů, takže když hledání vrátí blízký zásah, okolí dovede k tomu přesnému."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "predpis": {"type": "string", "description": "citace '89/2012 Sb.', '89/2012', nebo CELEX '32016R0679'"},
                "paragraf": {"type": "string", "description": "označení: '2235', '§ 2235', 'Čl. 5' nebo 'Článek 6' u předpisů EU"},
                "okoli": {"type": "integer", "description": "kolik ustanovení před a za (0–10, výchozí 0)"},
            },
            "required": ["predpis", "paragraf"],
        },
    },
    {
        "name": "predpis",
        "description": (
            "Metadata předpisu a jeho osnova (seznam paragrafů s nadpisy) — bez plného textu, "
            "aby se dal velký kodex prohlédnout lacino. Řekne i, jestli je předpis zrušený."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "citace": {"type": "string", "description": "např. '89/2012 Sb.' nebo CELEX '32016R0679'"},
                "osnova": {"type": "boolean", "description": "vypsat seznam paragrafů (výchozí ano)"},
            },
            "required": ["citace"],
        },
    },
    {
        "name": "judikatura",
        "description": (
            "Soudní rozhodnutí, která vykládají daný paragraf, se spisovou značkou a ECLI. "
            "Pokrytí: okresní, krajské a vrchní soudy od října 2020. Ústavní, Nejvyšší ani "
            "Nejvyšší správní soud v datech nejsou."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "predpis": {"type": "string", "description": "např. '89/2012 Sb.'"},
                "paragraf": {"type": "string", "description": "např. '2235'"},
                "limit": {"type": "integer", "description": "kolik rozhodnutí vypsat (výchozí 25, od nejnovějšího)"},
            },
            "required": ["predpis", "paragraf"],
        },
    },
    {
        "name": "vrcholne_soudy",
        "description": (
            "Judikatura Nejvyššího, Ústavního a Nejvyššího správního soudu — ta v rejstříku "
            "`judikatura` NENÍ, protože otevřená data justice.cz končí u vrchních soudů.\n\n"
            "Nejvyšší soud se prohledá rovnou a vrátí spisové značky; s `plny_text` přidá i znění "
            "prvního rozhodnutí. Ústavní a Nejvyšší správní soud strojově přístupné nejsou, "
            "u nich nástroj vrátí, kam jít a co tam zadat."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dotaz": {"type": "string", "description": "co hledat, např. 'výpověď z nájmu bytu'"},
                "paragraf": {"type": "string", "description": "místo dotazu: číslo ustanovení, např. '2235'"},
                "predpis": {"type": "string", "description": "k paragrafu, např. '89/2012'"},
                "plny_text": {"type": "boolean", "description": "přidat znění prvního rozhodnutí (výchozí ne)"},
                "limit": {"type": "integer", "description": "kolik rozhodnutí vypsat (výchozí 15)"},
            },
        },
    },
    {
        "name": "zmeny",
        "description": ("Co se při poslední aktualizaci sbírky změnilo — nové a novelizované "
                        "předpisy, u novel i dotčená ustanovení. Vrací markdown."),
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "kolik položek (výchozí 30)"}},
        },
    },
]


def spojeni() -> sqlite3.Connection:
    if not DB.exists():
        raise RuntimeError("index neexistuje — spusť nejdřív `python3 tools/index.py`")
    spoj = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    spoj.row_factory = sqlite3.Row
    # Nedostavěný nebo poškozený soubor se pozná až při čtení. Bez tohohle dostane agent
    # „file is not a database“ a neví, co s tím; návod na přestavbu mu pomůže.
    try:
        spoj.execute("SELECT 1 FROM predpis LIMIT 1").fetchone()
    except sqlite3.DatabaseError as e:
        spoj.close()
        raise RuntimeError(
            f"index je poškozený nebo nedostavěný ({e}) — smaž `.cache/index.db` "
            f"a spusť `python3 tools/index.py` znovu") from e
    # Index stažený z Releases může být starší než kód; bez kontroly by nástroje padaly
    # na „no such column“ uprostřed odpovědi.
    sloupce = {r["name"] for r in spoj.execute("PRAGMA table_info(predpis)")}
    if "pristi_zneni_od" not in sloupce:
        spoj.close()
        raise RuntimeError(
            "index je ze starší verze nástrojů — přestav ho (`python3 tools/index.py`) "
            "nebo stáhni aktuální z Releases (`gh release download index-latest`)")
    return spoj


# CELEX: 32016R0679, 31992L0043 — číslice, písmeno sektoru, číslo. Nikdy nenese „Sb.“
CELEX = re.compile(r"^[1-9]\d{4}[A-Z]{1,2}\d{4}", re.I)


def normalizuj_citaci(citace: str) -> str:
    c = " ".join(citace.split())
    if CELEX.match(c):
        return c.upper()
    # „89/2012 sb.“, „89/2012 SB“ i „89/2012 Sb. m. s.“ — píše se to různě, ať to nevadí.
    # Písmeno na začátku patří k číslu: n69/1968 Sb. (oznámení) a o49/2001 Sb. (opatření)
    # jsou vlastní řady, dohromady 6 350 předpisů.
    if (m := re.match(r"^([a-zA-Z]*\d+/\d{4})\s*(sb\.?\s*m\.?\s*s\.?|sb\.?)?$", c, re.I)):
        # Řady se číslují nezávisle: 1/2000 Sb. a 1/2000 Sb. m. s. jsou dva různé předpisy.
        rada = re.sub(r"[.\s]", "", (m.group(2) or "")).lower()
        return f"{m.group(1)} Sb. m. s." if rada == "sbms" else f"{m.group(1)} Sb."
    return c


# Dotaz, ve kterém si tazatel řídí hledání sám — do toho se nesahá.
VLASTNI_SYNTAXE = ("*", '"', " OR ", " NOT ", "NEAR(")

try:
    import simplemma
except ImportError:
    simplemma = None


def lemmatizuj_dotaz(dotaz: str) -> str:
    """Přidá k dotazu základní tvary slov, aby „bytu“ našlo „byt“.

    Index drží vedle původního textu i sloupec se základními tvary; bez rozšíření dotazu by
    byl k ničemu. Vrací prázdno, když simplemma není nebo když si tazatel řídí hledání sám."""
    if simplemma is None or any(z in dotaz for z in VLASTNI_SYNTAXE):
        return ""
    zaklady = []
    for s in re.findall(r"\w+", dotaz.lower(), re.UNICODE):
        try:
            z = simplemma.lemmatize(s, lang="cs")
        except Exception:  # noqa: BLE001
            continue
        if z != s:
            zaklady.append(z)
    return " ".join(zaklady)


def zkrat_na_kmeny(dotaz: str) -> str:
    """Z „najem bytu vypoved" udělá „naje* byt* vypove*".

    Index drží tvary slov, ne kmeny, takže dotaz v jiném pádu než text zákona nenajde nic.
    Skutečná lemmatizace češtiny by chtěla knihovnu navíc; tohle je hrubá náhražka, která se
    pouští teprve tehdy, když přesný dotaz selhal — tam už zhoršit co zhoršit není."""
    slova = []
    for s in dotaz.split():
        if len(s) >= 6:
            slova.append(s[:-2] + "*")
        elif len(s) >= 4:
            slova.append(s[:-1] + "*")
        else:
            slova.append(s)
    return " ".join(slova)


def normalizuj_paragraf(p: str, citace: str = "") -> str:
    p = " ".join(p.split())
    # „§2288“ bez mezery je běžný zápis, ale v indexu je „§ 2288“.
    p = re.sub(r"^(§+|Čl\.|čl\.|cl\.)(?=\S)", r"\1 ", p)
    if p.lower().startswith(("§", "čl.", "cl.", "článek", "clanek", "article")):
        if p.lower().startswith("cl."):
            return p.replace("cl.", "Čl.", 1)
        if p.lower().startswith(("clanek", "article")):
            return "Článek " + p.split(maxsplit=1)[-1]
        return p
    # Předpisy EU se člení na články, české na paragrafy.
    return f"Článek {p}" if CELEX.match(citace.strip()) else f"§ {p}"


def cele_cislo(hodnota, vychozi: int, nejvyse: int) -> int:
    """Limit od agenta může přijít jako text i nesmysl; hláška je lepší než pád."""
    try:
        return max(1, min(int(hodnota or vychozi), nejvyse))
    except (TypeError, ValueError):
        return vychozi


def hledej(a: dict) -> str:
    spoj = spojeni()
    limit = cele_cislo(a.get("limit"), 8, 40)

    dotaz = a["dotaz"]
    # Agent občas hledá předpis tím, že do fulltextu napíše jeho citaci. Lomítko přitom
    # rozbije syntaxi FTS5 a CELEX v textu předpisů nestojí, takže by dostal chybu nebo nic.
    if (m := re.fullmatch(r"\s*([a-zA-Z]*\d+/\d{4})(\s*Sb\.?( m\. s\.)?)?\s*", dotaz)):
        return (f"„{dotaz.strip()}“ vypadá jako citace předpisu, ne jako hledaný text. "
                f"Metadata a osnovu vrátí `predpis(\"{m.group(1)}\")`, konkrétní ustanovení "
                f"`paragraf(\"{m.group(1)}\", \"2288\")`. Fulltext hledá ve znění předpisů, "
                f"kde citace samotná většinou nestojí.")
    if re.fullmatch(r"\s*[1-9]\d{4}[A-Z]{1,2}\d{4}[\w()]*\s*", dotaz):
        return (f"„{dotaz.strip()}“ je CELEX, tedy identifikátor předpisu EU. Fulltext ho "
                f"nenajde, protože v textu nestojí — použij `predpis(\"{dotaz.strip()}\")` "
                f"nebo `paragraf(\"{dotaz.strip()}\", \"Článek 6\")`.")

    # Prefix kratší než tři znaky projde statisíce ustanovení: `a*` trvá na studeném indexu
    # přes sto vteřin a vrátí náhodný vzorek. Odmítnout je rychlejší i užitečnější.
    if (kratky := [s for s in dotaz.split() if s.endswith("*") and len(s.rstrip("*")) < 3]):
        return (f"Dotaz {', '.join(kratky)} je příliš obecný — hvězdička potřebuje aspoň tři "
                f"znaky před sebou, jinak projde statisíce ustanovení a vrátí náhodný vzorek. "
                f"Zkus delší kmen, například „najm*“ místo „na*“.")

    podminky, param = ["usek_fts MATCH ?"], [dotaz]
    if a.get("predpis"):
        podminky.append("p.citace = ?")
        param.append(normalizuj_citaci(a["predpis"]))
    if not a.get("i_historicke"):
        # Zrušený předpis a „úplné znění" jsou obojí historie, ne živé právo. Úplné znění je
        # navíc jen publikace textu jiného předpisu — vlastní záznam o zrušení nemá, protože
        # vazba visí na tom původním, takže by je filtr na zruseno_k nechal projít.
        podminky.append("(p.zruseno_k IS NULL OR p.zruseno_k = '')")
        podminky.append("p.uplne_zneni = 0")
    param.append(limit)

    # Vedle relevance rozhoduje autorita předpisu — logaritmus počtu rozhodnutí, která se ho
    # dovolávají. Bez ní bm25 staví nad kodexy okrajové předpisy, protože dělí skóre délkou
    # textu: na dotaz po výpovědi z nájmu přijde nařízení z roku 2021 místo občanského zákoníku.
    # Na zlaté sadě (tools/dotazy.py) to zvedlo trefu na správný předpis ze 75 % na 89 %.
    #
    # Přiklánět pořadí k novějším předpisům se zkusilo taky a bylo to měřitelně horší: kodexy
    # jsou staré, takže výhoda pro novější je vytlačí ve prospěch novel a prováděcích vyhlášek.
    # Úplná znění jdou vlastním klíčem dozadu pro případ, že si je někdo vyžádá přes i_historicke.
    sql = f"""
        SELECT p.citace, p.nazev, p.zruseno_k, u.oznaceni, u.nadpis, u.text, u.radek, p.soubor
        FROM usek_fts JOIN usek u ON u.id = usek_fts.rowid
        JOIN predpis p ON p.citace = u.predpis
        WHERE {' AND '.join(podminky)}
        ORDER BY p.uplne_zneni, bm25(usek_fts, 4.0, 3.0, 1.0, 1.0, 2.0) - COALESCE(p.autorita, 0)
        LIMIT ?
    """
    try:
        radky = spoj.execute(sql, param).fetchall()
    except sqlite3.OperationalError as e:
        spoj.close()
        # Jen chybějící modul; „fts5: syntax error near …“ je chyba v dotazu.
        if "no such module: fts5" in str(e):
            return BEZ_FTS5.format(verze=sys.executable)
        return f"Dotaz se nepodařilo přečíst ({e}). Zkontroluj uvozovky a závorky."

    # Dotaz se pokládá ve třech podobách naráz — doslova, přes základní tvary slov a přes
    # hrubé kmeny — a výsledky se slijí Reciprocal Rank Fusion: každý zásah dostane
    # 1/(60 + pořadí) z každého seznamu, ve kterém se objevil. Dřív se varianty zkoušely po
    # řadě a použila se první, která něco vrátila; když přesný dotaz vrátil jediný špatný
    # zásah, k lemmatizovanému se nikdy nedošlo. Měřeno na zlaté sadě: hledaný předpis ve
    # výsledcích 70 → 74 z 84, hledané ustanovení v první desítce 63 → 67.
    nahradni = ""
    if not any(z in dotaz for z in VLASTNI_SYNTAXE):
        ostatni = [v for v in (lemmatizuj_dotaz(dotaz), zkrat_na_kmeny(dotaz)) if v and v != dotaz]
        if ostatni:
            body: dict[int, float] = {}
            radek_podle_id: dict[int, sqlite3.Row] = {}
            for varianta in [dotaz, *ostatni]:
                param[0] = varianta
                param[-1] = 30
                try:
                    nalezy = spoj.execute(sql, param).fetchall()
                except sqlite3.OperationalError:
                    continue
                for poradi, r in enumerate(nalezy, start=1):
                    klic = (r["citace"], r["oznaceni"])
                    body[klic] = body.get(klic, 0.0) + 1.0 / (60 + poradi)
                    radek_podle_id.setdefault(klic, r)
            if body:
                poradi_klicu = sorted(body, key=lambda k: -body[k])[:limit]
                radky = [radek_podle_id[k] for k in poradi_klicu]
                if not spoj.execute(sql, [dotaz, *param[1:-1], 1]).fetchall():
                    nahradni = ostatni[0]
    spoj.close()

    if not radky:
        return (
            "Nic nenalezeno.\n\n"
            "Nejčastější příčina je skloňování: hledají se tvary slov, ne kmeny. "
            "Zkus hvězdičku („byt*\" místo „bytu\"), méně slov, nebo i_historicke=true.\n\n"
            "Druhá příčina je změna terminologie. Občanský zákoník z roku 2012 přejmenoval "
            "řadu institutů, takže starý termín v textu není: „věcné břemeno\" je dnes "
            "„služebnost\", „půjčka\" je „zápůjčka\", „nájemné z bytu\" je „nájemné\". "
            "Hledání je lexikální, synonyma nezná — zkus druhý termín."
        )

    casti = []
    if nahradni:
        casti.append(f"*Přesný tvar nic nenašel, hledalo se proto volněji: `{nahradni}`*")
    for r in radky:
        hlava = f"## {r['citace']} {r['oznaceni']}"
        if r["zruseno_k"]:
            hlava += f"  ⚠ předpis zrušen k {r['zruseno_k']}"
        radek = [hlava, f"*{r['nazev']}*"]
        if r["nadpis"]:
            radek.append(f"**{r['nadpis']}**")
        text = r["text"]
        radek.append(text if len(text) <= 1500 else text[:1500] + " …")
        radek.append(f"`{r['soubor']}:{r['radek']}`")
        casti.append("\n".join(radek))
    return "\n\n---\n\n".join(casti)


def varianty_oznaceni(oznaceni: str) -> tuple[str, str, str, str, str, str]:
    """Tvary, pod kterými může ustanovení v indexu stát.

    Smlouvy mívají označení verzálkami („ČLÁNEK 1“) a člení se na články i tam, kde se z holého
    čísla doplní paragraf. SQLite upper() neumí diakritiku, takže se varianty skládají tady."""
    cislo = oznaceni.lstrip("§ ").strip()
    clanek = f"Článek {cislo}"
    return (oznaceni, oznaceni.upper(), oznaceni.lower(),
            clanek, clanek.upper(), f"Čl. {cislo}")


def pristi_zneni(od: str) -> str:
    return (f"⚠ **Od {od} platí nové znění** (novela je už vyhlášená). Text tady je znění platné "
            f"dnes; u lhůt a vztahů, které přesáhnou {od}, ověř nové znění v e-Sbírce.")


def chybi_predpis(citace: str, zadano: str) -> str:
    """Proč předpis v indexu není — agent musí poznat překlep od mezery ve sbírce."""
    if not re.match(r"^[no]?\d+/\d{4}", citace) and not CELEX.match(citace):
        return (f"„{zadano}“ nevypadá jako citace. Předpis se hledá číslem a rokem "
                f"(`89/2012 Sb.`) nebo CELEXem (`32016R0679`); podle názvu ho najdeš "
                f"nástrojem `hledej`.")
    if CELEX.match(citace) and not re.match(r"^3\d{4}[RL]", citace):
        # Agent jinak nepozná, jestli předpis neexistuje, nebo jen není v téhle sbírce.
        return (f"{citace} v indexu není: z práva EU jsou tu jen nařízení (R) a směrnice (L), "
                f"ne rozhodnutí, smlouvy ani judikatura. Znění najdeš na "
                f"https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:{citace}")
    return f"Předpis {citace} v indexu není."


def paragraf(a: dict) -> str:
    # Smlouvy mívají označení verzálkami („ČLÁNEK 1“), zákony ne. SQLite upper() neumí
    # diakritiku (z „Článek“ udělá „ČLáNEK“), takže se varianty skládají v Pythonu.
    spoj = spojeni()
    citace = normalizuj_citaci(a["predpis"])
    oznaceni = normalizuj_paragraf(a["paragraf"], citace)
    vsechny = spoj.execute("""
        SELECT p.citace, p.nazev, p.zruseno_k, p.zrusil, p.pristi_zneni_od, u.id, u.oznaceni,
               u.nadpis, u.text, u.radek, p.soubor
        FROM usek u JOIN predpis p ON p.citace = u.predpis
        WHERE u.predpis = ? AND u.oznaceni IN (?, ?, ?, ?, ?, ?)
        ORDER BY u.radek
    """, (citace, *varianty_oznaceni(oznaceni))).fetchall()
    r = vsechny[0] if vsechny else None

    if not r:
        existuje = spoj.execute("SELECT 1 FROM predpis WHERE citace = ?", (citace,)).fetchone()
        spoj.close()
        if not existuje:
            return chybi_predpis(citace, a["predpis"])
        return f"{citace} {oznaceni} v indexu není."

    casti = [f"# {r['citace']} {r['oznaceni']}", f"*{r['nazev']}*"]

    # Smlouvy mívají desítky protokolů a příloh, každou s vlastním § 1 — u COTIF je jich 178.
    # Bez upozornění by agent citoval ustanovení jedné přílohy v domnění, že je ze smlouvy.
    if len(vsechny) > 1:
        kde = ", ".join(f"řádek {x['radek']}" for x in vsechny[1:6])
        dalsi = f" a {len(vsechny) - 6} dalších" if len(vsechny) > 6 else ""
        casti.append(
            f"\n⚠ **Označení {oznaceni} je v tomto předpisu {len(vsechny)}×** — předpis má víc "
            f"částí s vlastním číslováním (přílohy, protokoly). Níže je první z nich; ostatní "
            f"jsou na {kde}{dalsi} v `{r['soubor']}`.")
    if r["zruseno_k"]:
        casti.append(f"⚠ **Předpis byl zrušen k {r['zruseno_k']}**" + (f", a to předpisem {r['zrusil']}" if r["zrusil"] else "."))
    if r["pristi_zneni_od"]:
        casti.append(pristi_zneni(r["pristi_zneni_od"]))
    if r["nadpis"]:
        casti.append(f"**{r['nadpis']}**")
    casti.append(orizni(r["text"], f"{r['soubor']}:{r['radek']}"))
    casti.append(f"`{r['soubor']}:{r['radek']}`")

    # Sousedi se berou podle pořadí v předpisu (id roste, jak text jde za sebou), ne podle
    # čísla — § 141a leží mezi 141 a 142 a číselné řazení by ho minulo.
    okoli = cele_cislo(a.get("okoli"), 0, 10) if a.get("okoli") else 0
    if okoli:
        sousedi = spoj.execute("""
            SELECT oznaceni, nadpis, text FROM usek
            WHERE predpis = ? AND id BETWEEN ? AND ? AND id != ?
            ORDER BY id
        """, (citace, r["id"] - okoli, r["id"] + okoli, r["id"])).fetchall()
        if sousedi:
            casti.append(f"## Okolní ustanovení ({len(sousedi)})")
            for s in sousedi:
                hlava = f"### {s['oznaceni']}" + (f" — {s['nadpis']}" if s["nadpis"] else "")
                text = s["text"]
                casti.append(hlava + "\n" + (text if len(text) <= 900 else text[:900] + " …"))

    spoj.close()
    return "\n\n".join(casti)


def predpis(a: dict) -> str:
    spoj = spojeni()
    citace = normalizuj_citaci(a["citace"])
    p = spoj.execute("SELECT * FROM predpis WHERE citace = ?", (citace,)).fetchone()
    if not p:
        spoj.close()
        return chybi_predpis(citace, a["citace"])

    casti = [f"# {p['citace']} — {p['nazev']}"]
    popis = [f"druh: {p['druh']}" if p["druh"] else "", f"účinnost znění od: {p['ucinnost_od']}" if p["ucinnost_od"] else ""]
    casti.append("\n".join(x for x in popis if x))
    if p["zruseno_k"]:
        casti.append(f"⚠ **Zrušen k {p['zruseno_k']}**" + (f", a to předpisem {p['zrusil']}" if p["zrusil"] else "."))
    else:
        casti.append("Zrušení není v datech zaznamenáno. Pozor: u starších předpisů to neznamená, že platí.")
    if p["pristi_zneni_od"]:
        casti.append(pristi_zneni(p["pristi_zneni_od"]))
    if p["eli"]:
        casti.append(f"ELI: `{p['eli']}` — https://e-sbirka.gov.cz{p['eli']}")
    casti.append(f"soubor: `{p['soubor']}`")

    if a.get("osnova", True):
        useky = spoj.execute(
            "SELECT oznaceni, nadpis FROM usek WHERE predpis = ? ORDER BY id LIMIT 800", (citace,)
        ).fetchall()
        if useky:
            casti.append(f"## Osnova ({len(useky)} ustanovení)")
            casti.append("\n".join(f"- {u['oznaceni']}" + (f" — {u['nadpis']}" if u["nadpis"] else "") for u in useky))
    spoj.close()
    return "\n\n".join(casti)


def judikatura(a: dict) -> str:
    citace = normalizuj_citaci(a["predpis"])
    # Rejstřík staví na otevřených datech justice.cz, kde české soudy citují české předpisy.
    # K CELEXu v něm nikdy nic nebude, tak se to neschovává za „není dost rozhodnutí“.
    if CELEX.match(citace):
        return (f"K předpisům EU rejstřík judikatury není — staví na rozhodnutích českých soudů, "
                f"která citují Sbírku zákonů, ne CELEXy.\n\n"
                f"Judikaturu k {citace} hledej u Soudního dvora EU na https://curia.europa.eu "
                f"nebo v EUR-Lexu (https://eur-lex.europa.eu/collection/n-law/eu-case-law.html). "
                f"Rozhodnutí českých soudů k tuzemské úpravě téhož tématu najdeš přes tento "
                f"nástroj pod českou citací.")
    cislo, _, rok = citace.replace(" Sb.", "").partition("/")
    # Rejstřík vede písmeno za číslem malé (§ 14b), agent může napsat „14B“.
    par = normalizuj_paragraf(a["paragraf"], citace).replace("§", "").strip().lower()
    # Cesta se skládá ze vstupu, takže „../“ v označení by pustilo čtení mimo repozitář.
    # Ověřuje se výsledná cesta, ne vstup: samotné filtrování „..“ obchází kódování.
    cesta = (JUDIKATURA / f"{cislo}-{rok}" / f"{par}.md").resolve()
    if not cesta.is_relative_to(JUDIKATURA.resolve()):
        return f"Neplatné označení ustanovení: {a['paragraf']!r}."
    if not cesta.exists():
        return f"K {citace} § {par} nejsou v rejstříku žádná rozhodnutí (rejstřík vede jen paragrafy s aspoň třemi)."

    obsah = cesta.read_text(encoding="utf-8")
    # U frekventovaných paragrafů je rozhodnutí přes pět set a celý soubor má 30 kB. To je
    # proti smyslu serveru, který má agentovi šetřit kontext, tak se vrací jen začátek tabulky.
    # Rozhodnutí jsou řazená od nejnovějšího, takže ořez bere ta čerstvá.
    limit = cele_cislo(a.get("limit"), 25, 200)
    radky = obsah.split("\n")
    zacatek = next((i for i, r in enumerate(radky) if r.startswith("|---")), None)
    if zacatek is None:
        return obsah
    hlavicka, tabulka = radky[:zacatek + 1], [r for r in radky[zacatek + 1:] if r.startswith("|")]
    if len(tabulka) <= limit:
        return obsah
    zbytek = len(tabulka) - limit
    # Rejstřík sám vede jen 300 nejnovějších, takže vyšší limit nedá víc než tolik — slibovat
    # agentovi všechna rozhodnutí by bylo zavádějící.
    return "\n".join(hlavicka + tabulka[:limit]) + (
        f"\n\nVypsáno {limit} nejnovějších, dalších {zbytek} z rejstříku je vynecháno "
        f"(vyšší `limit` je ukáže). Rejstřík sám vede 300 nejnovějších; starší rozhodnutí "
        f"jsou v `judikatura/metadata/`.\n")


def zmeny(a: dict) -> str:
    if not ZMENY.exists():
        return "Přehled změn zatím neexistuje — spusť `python3 tools/changelog.py`."
    data = json.loads(ZMENY.read_text(encoding="utf-8"))
    limit = cele_cislo(a.get("limit"), 30, 200)
    zaznamy = data if isinstance(data, list) else data.get("zaznamy", [])
    if not zaznamy:
        return "Při poslední aktualizaci se nic nezměnilo."

    # Markdown, ne syrový JSON: ostatní nástroje vrací text ke čtení a agent nemá mít
    # povinnost parsovat, aby zjistil, co přibylo.
    radky = [f"# Změny z {data.get('datum', '?')}" if isinstance(data, dict) else "# Změny", ""]
    radky.append(f"Celkem {len(zaznamy)} předpisů; vypsáno {min(limit, len(zaznamy))}.")
    radky.append("")
    for z in zaznamy[:limit]:
        druh = z.get("druh", "")
        radky.append(f"- **{z.get('citace', '?')}** ({druh}) — {z.get('nazev', '')[:90]}")
        if (novel := z.get("novelizovano")):
            radky.append(f"    novelizuje: {', '.join(str(n) for n in novel[:6])}")
        if (par := z.get("paragrafy")):
            radky.append(f"    dotčená ustanovení: {', '.join(str(x) for x in par[:10])}")
    if len(zaznamy) > limit:
        radky.append(f"\nDalších {len(zaznamy) - limit} je vynecháno; vyžádej si je vyšším `limit`.")
    return "\n".join(radky)


def vrcholne_soudy(a: dict) -> str:
    sys.path.insert(0, str(KOREN / "tools"))
    import vrcholne  # noqa: PLC0415 — načítá se až při volání, ať server nastartuje i bez sítě

    dotaz = a.get("dotaz", "")
    if a.get("paragraf"):
        par = str(a["paragraf"]).lstrip("§ ").strip()
        dotaz = f'"§ {par}"'
        nazvy = {"89/2012": "občanského zákoníku", "262/2006": "zákoníku práce",
                 "99/1963": "občanského soudního řádu", "40/2009": "trestního zákoníku",
                 "500/2004": "správního řádu", "141/1961": "trestního řádu"}
        if (slovy := nazvy.get((a.get("predpis") or "").replace(" Sb.", ""))):
            dotaz += f" AND {slovy}"
    if not dotaz:
        return vrcholne.kam_jit()

    limit = cele_cislo(a.get("limit"), 15, 40)
    try:
        nalezy = vrcholne.hledej(dotaz, limit)
    except Exception as e:  # noqa: BLE001 — výpadek cizího webu nesmí shodit nástroj
        # Návod na `vrcholne.py` je tu k ničemu — ten sahá na tentýž web. Zbývají odkazy,
        # které si člověk otevře v prohlížeči.
        return (f"Databáze Nejvyššího soudu teď neodpovídá ({type(e).__name__}). Zkus to za chvíli; "
                f"rejstřík judikatury nižších soudů v repozitáři funguje i bez sítě "
                f"(nástroj `judikatura`).\n\n"
                f"Ručně: https://rozhodnuti.nsoud.cz (Nejvyšší soud), https://nalus.usoud.cz "
                f"(Ústavní soud), https://vyhledavac.nssoud.cz (Nejvyšší správní soud).")

    casti = [f"# Nejvyšší soud — „{dotaz}“", "", f"Nalezeno {len(nalezy)} rozhodnutí."]
    if nalezy:
        casti += ["", *[f"- {n['znacka']} — {n['odkaz']}" for n in nalezy]]

    if a.get("plny_text") and nalezy:
        try:
            r = vrcholne.rozhodnuti(nalezy[0]["odkaz"])
            casti += ["", f"## {r['znacka']} ({r['datum']})", f"`{r['ecli']}`", "",
                      r["text"] if len(r["text"]) <= 6000 else r["text"][:6000] + " …"]
        except Exception as e:  # noqa: BLE001
            casti.append(f"\nPlný text se nepodařilo stáhnout: {type(e).__name__}")

    casti += ["", "---", "", vrcholne.kam_jit()]
    return "\n".join(casti)


OBSLUHA = {"hledej": hledej, "paragraf": paragraf, "predpis": predpis, "judikatura": judikatura,
           "vrcholne_soudy": vrcholne_soudy, "zmeny": zmeny}


def odpovez(zprava: dict) -> dict | None:
    metoda, ident = zprava.get("method"), zprava.get("id")

    if metoda == "initialize":
        zadana = (zprava.get("params") or {}).get("protocolVersion")
        return {"jsonrpc": "2.0", "id": ident, "result": {
            "protocolVersion": zadana if isinstance(zadana, str) else VERZE,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "czech-law-md", "version": VERZE_SERVERU},
        }}

    if metoda in ("notifications/initialized", "notifications/cancelled"):
        return None

    if metoda == "ping":
        return {"jsonrpc": "2.0", "id": ident, "result": {}}

    if metoda == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": NASTROJE}}

    if metoda == "tools/call":
        params = zprava.get("params") or {}
        jmeno = params.get("name")
        if jmeno not in OBSLUHA:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": f"neznámý nástroj: {jmeno}"}}
        try:
            text = OBSLUHA[jmeno](params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": text}]}}
        except KeyError as e:
            # Chybějící povinný parametr; holé KeyError agentovi neřekne, co doplnit.
            povinne = next((n["inputSchema"].get("required", []) for n in NASTROJE
                            if n["name"] == jmeno), [])
            return {"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text":
                f"Nástroji `{jmeno}` chybí parametr {e}. Povinné jsou: "
                f"{', '.join(povinne) if povinne else 'žádné'}."}], "isError": True}}
        except (TypeError, AttributeError) as e:
            # Parametr přišel jako seznam, číslo nebo null místo textu.
            return {"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text":
                f"Nástroj `{jmeno}` dostal parametr ve špatném tvaru ({e}). Citace, označení "
                f"i dotaz se předávají jako text, limity jako celé číslo."}], "isError": True}}
        except Exception as e:  # noqa: BLE001 — chyba nástroje se hlásí klientovi, server běží dál
            return {"jsonrpc": "2.0", "id": ident, "result": {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}}

    if ident is None:
        return None
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"neznámá metoda: {metoda}"}}


def main() -> int:
    for radek in sys.stdin:
        radek = radek.strip()
        if not radek:
            continue
        try:
            zprava = json.loads(radek)
        except json.JSONDecodeError:
            continue
        odpoved = odpovez(zprava)
        if odpoved is not None:
            print(json.dumps(odpoved, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
