"""Testy nástrojů — aby se znovu nerozbilo to, co už jednou rozbité bylo.

Každý test tu je proto, že odpovídající chyba se skutečně stala. `kontrola.py` hlídá data,
`dotazy.py` kvalitu hledání; tohle hlídá chování nástrojů: co vrátí MCP na okrajový vstup,
jestli převod XML dá nadpis správnému paragrafu, jestli se cizí rozhodnutí nevydává za žádané.

    python3 tools/testy.py            # vše, co nepotřebuje síť
    python3 tools/testy.py --se-siti  # i živé dotazy na e-Sbírku a Nejvyšší soud
    python3 tools/testy.py -v         # vypsat i to, co prošlo
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
DB = KOREN / ".cache" / "index.db"
sys.path.insert(0, str(KOREN / "tools"))

selhani: list[str] = []
proslo = 0
podrobne = False


def overit(podminka: bool, popis: str, detail: str = "") -> None:
    global proslo
    if podminka:
        proslo += 1
        if podrobne:
            print(f"  ✓ {popis}")
    else:
        selhani.append(f"{popis}{f' — {detail}' if detail else ''}")
        print(f"  ✗ {popis}{f' — {detail}' if detail else ''}")


def mcp(volani: list[tuple[str, dict]]) -> list[dict]:
    """Pustí MCP server a projede jím volání. Vrací výsledky v pořadí zadání."""
    zpravy = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18"}}]
    for i, (jmeno, argumenty) in enumerate(volani, start=2):
        zpravy.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": jmeno, "arguments": argumenty}})
    beh = subprocess.run(
        [sys.executable, str(KOREN / "tools" / "mcp.py")],
        input="\n".join(json.dumps(z, ensure_ascii=False) for z in zpravy) + "\n",
        capture_output=True, text=True, timeout=600)
    vysledky: dict[int, dict] = {}
    for radek in beh.stdout.strip().split("\n"):
        if radek.strip():
            d = json.loads(radek)
            if d.get("id", 0) >= 2:
                vysledky[d["id"]] = d.get("result", {})
    return [vysledky.get(i, {}) for i in range(2, len(volani) + 2)]


def text(vysledek: dict) -> str:
    return vysledek.get("content", [{}])[0].get("text", "")


def test_normalizace() -> None:
    """Citace a označení ustanovení. EU se člení na články a nikdy nenese „Sb.“"""
    import mcp as server

    overit(server.normalizuj_citaci("89/2012") == "89/2012 Sb.", "citace doplní Sb.")
    overit(server.normalizuj_citaci("89/2012 Sb.") == "89/2012 Sb.", "citace se Sb. zůstane")
    overit(server.normalizuj_citaci("32016R0679") == "32016R0679", "CELEX nedostane Sb.")
    overit(server.normalizuj_citaci("32016r0679") == "32016R0679", "CELEX se zvelkopísmení")

    overit(server.normalizuj_paragraf("2288", "89/2012 Sb.") == "§ 2288", "české číslo dostane §")
    overit(server.normalizuj_paragraf("§ 2288", "89/2012 Sb.") == "§ 2288", "§ se nezdvojí")
    overit(server.normalizuj_paragraf("6", "32016R0679") == "Článek 6", "u EU se doplní Článek")
    overit(server.normalizuj_paragraf("Článek 6", "32016R0679") == "Článek 6", "Článek se nezdvojí")
    overit(server.normalizuj_paragraf("Article 6", "32016R0679") == "Článek 6", "Article se počeští")

    # Běžné překlepy, se kterými agent přijde.
    overit(server.normalizuj_citaci("89/2012 sb.") == "89/2012 Sb.", "malé „sb.“ se srovná")
    overit(server.normalizuj_citaci("  89/2012  ") == "89/2012 Sb.", "mezery navíc nevadí")
    overit(server.normalizuj_paragraf("§2288", "89/2012 Sb.") == "§ 2288", "§ bez mezery se srovná")
    overit(server.normalizuj_paragraf("Čl.5", "89/2012 Sb.") == "Čl. 5", "Čl. bez mezery se srovná")

    # Řady se číslují nezávisle: 1/2000 Sb. a 1/2000 Sb. m. s. jsou dva různé předpisy.
    overit(server.normalizuj_citaci("1/2000 Sb. m. s.") == "1/2000 Sb. m. s.",
           "řada mezinárodních smluv se nezahodí")
    overit(server.normalizuj_citaci("1/2000 sb.m.s.") == "1/2000 Sb. m. s.",
           "řada smluv se pozná i bez mezer")
    overit(server.normalizuj_citaci("1/2000") == "1/2000 Sb.", "bez řady se doplní Sb.")

    # Vlastní řady s písmenem: n69/1968 Sb. (oznámení), o49/2001 Sb. (opatření) — 6 350 předpisů.
    overit(server.normalizuj_citaci("n69/1968") == "n69/1968 Sb.", "prefix n se zachová")
    overit(server.normalizuj_citaci("o49/2001") == "o49/2001 Sb.", "prefix o se zachová")
    overit(server.normalizuj_citaci("n1/2004 sb.m.s.") == "n1/2004 Sb. m. s.",
           "prefix i řada smluv naráz")


def test_mcp_nastroje() -> None:
    """Co MCP vrátí na běžný i okrajový vstup."""
    if not DB.exists():
        overit(False, "index existuje", "spusť tools/index.py")
        return

    v = mcp([
        ("paragraf", {"predpis": "89/2012", "paragraf": "2288"}),
        ("paragraf", {"predpis": "32016R0679", "paragraf": "Článek 6"}),
        ("paragraf", {"predpis": "32016R0679", "paragraf": "6"}),
        ("paragraf", {"predpis": "89/2012", "paragraf": "99999"}),
        # Smlouvy mívají označení verzálkami a člení se na články i tam, kde agent napíše
        # holé číslo; SQLite upper() přitom neumí diakritiku.
        ("paragraf", {"predpis": "1/2021 Sb. m. s.", "paragraf": "Článek 1"}),
        ("paragraf", {"predpis": "1/2021 Sb. m. s.", "paragraf": "1"}),
        ("predpis", {"citace": "89/2012 Sb.", "osnova": False}),
        # Kouřová zkouška, ne měření řazení — to dělá zlatá sada. Při limitu 3 test padal na
        # posun o jedno místo po opravě nadpisů v občanském zákoníku.
        ("hledej", {"dotaz": "vypove* najm* byt*", "limit": 8}),
        ("hledej", {"dotaz": "naprosto nesmyslny dotaz xyzzy qwerty", "limit": 3}),
        # Citace v fulltextu: lomítko rozbíjí syntaxi FTS5 a CELEX v textu nestojí.
        ("hledej", {"dotaz": "89/2012", "limit": 2}),
        ("hledej", {"dotaz": "32016R0679", "limit": 2}),
        ("hledej", {"dotaz": '"§ 2288"', "limit": 3}),
        ("judikatura", {"predpis": "89/2012 Sb.", "paragraf": "2235"}),
        ("judikatura", {"predpis": "89/2012 Sb.", "paragraf": "2288"}),
        ("judikatura", {"predpis": "89/2012 Sb.", "paragraf": "2288", "limit": 100}),
        ("hledej", {"dotaz": "zpracovan* udaj*", "predpis": "32016R0679", "limit": 2}),
        ("hledej", {"dotaz": "najem AND ((", "limit": 2}),
        ("hledej", {"dotaz": "", "limit": 2}),
        ("zmeny", {"limit": 3}),
        ("predpis", {"citace": "32016R0679"}),
        ("judikatura", {"predpis": "32016R0679", "paragraf": "Článek 6"}),
        ("predpis", {"citace": "1/2000 Sb. m. s.", "osnova": False}),
        ("predpis", {"citace": "1/2000 Sb.", "osnova": False}),
        ("predpis", {"citace": "občanský zákoník"}),
    ])

    overit("§ 2288" in text(v[0]), "paragraf najde české ustanovení")
    overit("Článek 6" in text(v[1]) and "není" not in text(v[1])[:60],
           "paragraf najde článek předpisu EU", text(v[1])[:70])
    overit("Článek 6" in text(v[2]) and "není" not in text(v[2])[:60],
           "paragraf u EU zvládne i holé číslo", text(v[2])[:70])
    overit("není" in text(v[3]), "neexistující ustanovení se ohlásí")
    overit("ČLÁNEK 1" in text(v[4]), "označení verzálkami se najde i malými písmeny", text(v[4])[:50])
    overit("ČLÁNEK 1" in text(v[5]), "holé číslo u smlouvy najde článek", text(v[5])[:50])
    overit("občanský zákoník" in text(v[6]).lower(), "predpis vrátí metadata")
    overit("2288" in text(v[7]), "hledej najde výpověď z nájmu")
    overit(text(v[8]).strip() != "" and "Nic nenalezeno" in text(v[8]),
           "prázdný výsledek se ohlásí srozumitelně")
    overit("citace předpisu" in text(v[9]), "citace v fulltextu poradí správný nástroj", text(v[9])[:50])
    overit("CELEX" in text(v[10]), "CELEX v fulltextu poradí správný nástroj", text(v[10])[:50])
    overit("2288" in text(v[11]), "hledej zvládne frázi s §")
    overit(text(v[12]).strip() != "", "judikatura vrátí odpověď")

    # Smlouvy mívají desítky příloh s vlastním § 1; bez upozornění by agent citoval
    # ustanovení jedné z nich v domnění, že je ze smlouvy.
    vicekrat = mcp([("paragraf", {"predpis": "65/2016 Sb. m. s.", "paragraf": "1"})])[0]
    # Konkrétní počet se mění s tím, kolik tvarů označení nástroj zkouší; test hlídá
    # jen to, že se víceznačnost vůbec ohlásí.
    import re as _re2
    m = _re2.search(r"je v tomto předpisu (\d+)×", text(vicekrat))
    overit("⚠" in text(vicekrat) and m and int(m.group(1)) > 100,
           "paragraf hlásí, že označení je v předpisu víckrát",
           text(vicekrat)[:70])
    # Jiná ⚠ tam být smějí: občanský zákoník má ohlášené znění od 1. 1. 2027.
    overit("je v tomto předpisu" not in text(v[0]), "u jednoznačného ustanovení varování není")
    overit("platí nové znění" in text(v[0]), "paragraf ohlásí chystané nové znění", text(v[0])[:200])

    # § 3 zákona 1/1998 Sb. (celní sazebník) má přes sedm milionů znaků, tedy zhruba
    # 1,7 milionu tokenů. Server má kontext šetřit, ne ho zahltit.
    obri = text(mcp([("paragraf", {"predpis": "1/1998", "paragraf": "3"})])[0])
    overit(len(obri) < 60000, "obří ustanovení se ořízne", f"{len(obri):,} znaků")
    overit("Zkráceno" in obri, "oříznutí se přizná i s cestou k celému textu")

    # Frekventovaný paragraf má přes pět set rozhodnutí; bez limitu to byl 30 kB kontextu.
    vychozi, vetsi = text(v[13]), text(v[14])
    overit(len(vychozi) < 6000, "judikatura nezaplaví kontext", f"{len(vychozi)} znaků")
    overit(sum(1 for r in vychozi.split(chr(10)) if r.startswith("| 2")) == 25,
           "judikatura vypíše ve výchozím stavu 25 rozhodnutí")
    overit(len(vetsi) > len(vychozi), "vyšší limit vypíše víc")
    overit("vynecháno" in vychozi, "ořez se přizná")

    overit("32016R0679" in text(v[15]), "filtr na předpis funguje i pro CELEX")
    overit("nepodařilo" in text(v[16]), "rozbitá syntaxe dotazu se ohlásí, ne spadne")
    overit(text(v[17]).strip() != "", "prázdný dotaz nespadne", "prázdná odpověď")
    overit(text(v[18]).lstrip().startswith("#"),
           "zmeny vrací markdown, ne syrový JSON", text(v[18])[:40])
    overit("NAŘÍZENÍ" in text(v[19]) or "2016/679" in text(v[19]),
           "predpis funguje pro CELEX bez Sb.")
    overit("curia" in text(v[20]).lower() and "§ Článek" not in text(v[20]),
           "judikatura u EU nasměruje na Soudní dvůr, ne na chybějící rejstřík",
           text(v[20])[:70])

    smlouva, zakon = text(v[21]), text(v[22])
    overit("Sb. m. s." in smlouva and "není" not in smlouva[:40],
           "1/2000 Sb. m. s. se najde jako smlouva", smlouva[:60])
    overit("Sb. m. s." not in zakon.split(chr(10))[0],
           "1/2000 Sb. je jiný předpis než 1/2000 Sb. m. s.", zakon[:60])
    overit("hledej" in text(v[23]),
           "název místo citace poradí, jak předpis najít", text(v[23])[:70])


def test_popisy_nastroju() -> None:
    """Agent si nástroj vybírá podle popisu. Když v něm právo EU není, nenajde ho."""
    import mcp as server

    podle_jmena = {n["name"]: n for n in server.NASTROJE}
    for jmeno in ("hledej", "paragraf", "predpis"):
        n = podle_jmena[jmeno]
        vse = n["description"] + json.dumps(n["inputSchema"], ensure_ascii=False)
        overit("CELEX" in vse or " EU" in vse, f"popis `{jmeno}` zmiňuje právo EU")

    overit("Článek" in podle_jmena["paragraf"]["description"],
           "popis `paragraf` říká, že u EU se člení na články")
    for n in server.NASTROJE:
        overit(len(n["description"]) > 40, f"popis `{n['name']}` není prázdný")


def test_retezec_nastroju() -> None:
    """Co vrátí `hledej`, musí jít vytáhnout `paragrafem`, a odkaz musí vést na to ustanovení.

    Řetězec hledej → paragraf → judikatura je hlavní pracovní postup agenta. Kdyby se v něm
    rozešlo označení ustanovení, agent najde text, ale nedostane se k jeho plnému znění."""
    import re

    if not DB.exists():
        overit(False, "index existuje", "spusť tools/index.py")
        return

    dotazy = ["vypove* najm* byt*", "zpracovan* udaj* zakonn*", "promlc* lhut*"]
    vysledky = mcp([("hledej", {"dotaz": d, "limit": 3}) for d in dotazy])

    dvojice, odkazy = [], []
    for v in vysledky:
        posledni = ""
        for radek in text(v).split("\n"):
            if radek.startswith("## "):
                cast = radek[3:].split("  ")[0].strip()
                if (m := re.match(r"^(.+?)\s+((?:§|Čl\.|Článek)\s*\S+|\(bez členění\))$", cast)):
                    dvojice.append((m.group(1).strip(), m.group(2).strip()))
                    posledni = m.group(2).strip()
            # Předpis bez členění žádný nadpis ustanovení nemá, takže na něj řádek neukazuje.
            elif (m := re.search(r"`([^`]+\.md):(\d+)`", radek)) and posledni != "(bez členění)":
                odkazy.append((m.group(1), m.group(2)))

    overit(len(dvojice) >= 5, "hledej vrátil ustanovení k ověření", f"{len(dvojice)}")
    zpet = mcp([("paragraf", {"predpis": c, "paragraf": par}) for c, par in dvojice])
    chybi = [f"{c} {par}" for (c, par), v in zip(dvojice, zpet) if "v indexu není" in text(v)]
    overit(not chybi, "každé nalezené ustanovení jde vytáhnout paragrafem", "; ".join(chybi[:3]))

    # Zastaralý index není vada kódu, ale nesmí se tvářit jako správná odpověď. Pozná se tak,
    # že ustanovení v souboru je, jen na jiném řádku — proti tomu skutečná vada výpočtu by ho
    # nenašla nikde. Časy souborů na to nestačí: stavba indexu trvá minuty a soubor se může
    # změnit v jejím průběhu.
    spatne, zastarale = [], []
    for (citace, oznaceni), (cesta, cislo) in zip(dvojice, odkazy):
        soubor = KOREN / cesta
        if not soubor.exists():
            spatne.append(f"{cesta} neexistuje")
            continue
        radky = soubor.read_text(encoding="utf-8").split("\n")
        n = int(cislo)
        obsah = radky[n - 1] if 0 < n <= len(radky) else ""
        if obsah.lstrip().startswith("#"):
            continue
        jinde = any(r.lstrip().startswith("#") and oznaceni in r for r in radky)
        (zastarale if jinde else spatne).append(f"{cesta}:{cislo} ({oznaceni})")

    overit(not spatne, "odkaz soubor:řádek vede na nadpis ustanovení", "; ".join(spatne[:2]))
    overit(not zastarale, "index odpovídá souborům",
           f"{len(zastarale)} odkazů ukazuje vedle, protože se předpisy po indexaci změnily "
           f"— spusť `python3 tools/index.py` ({'; '.join(zastarale[:2])})")


def test_parametry_hledani() -> None:
    """Filtry a vlastní syntaxe musí přežít slévání variant dotazu.

    RRF pouští dotaz ve třech podobách naráz; kdyby se přitom ztratil filtr na předpis nebo
    se sáhlo do dotazu, který si tazatel řídí sám, hledání by tiše vracelo něco jiného."""
    if not DB.exists():
        overit(False, "index existuje", "spusť tools/index.py")
        return

    v = mcp([
        ("hledej", {"dotaz": "vypoved najem", "predpis": "89/2012 Sb.", "limit": 3}),
        ("hledej", {"dotaz": "zpracovani udaju", "predpis": "32016R0679", "limit": 3}),
        # Dotaz mířený na zrušený předpis, ne obecný: jinak test závisí na tom, jestli se
        # zrušený předpis náhodou vejde do prvních výsledků.
        ("hledej", {"dotaz": "zpracovan* udaj* svobod* pohyb*", "predpis": "31995L0046", "limit": 4}),
        ("hledej", {"dotaz": "zpracovan* udaj* svobod* pohyb*", "predpis": "31995L0046",
                    "i_historicke": True, "limit": 4}),
        ("hledej", {"dotaz": "NEAR(najem vypoved, 3)", "limit": 4}),
        ("hledej", {"dotaz": "NEAR(najem vypoved, 40)", "limit": 4}),
    ])

    def zasahy(vysledek):
        return [r[3:].split("  ")[0] for r in text(vysledek).split("\n") if r.startswith("## ")]

    mimo = [z for z in zasahy(v[0]) if not z.startswith("89/2012")]
    overit(not mimo, "filtr na český předpis drží i při slévání variant", str(mimo[:2]))
    mimo_eu = [z for z in zasahy(v[1]) if not z.startswith("32016R0679")]
    overit(not mimo_eu, "filtr na CELEX drží i při slévání variant", str(mimo_eu[:2]))

    bez, s_historii = set(zasahy(v[2])), set(zasahy(v[3]))
    overit(not bez, "zrušený předpis se ve výchozím stavu nevrací", f"vrátil {len(bez)}")
    overit(s_historii, "i_historicke zrušený předpis zpřístupní", "nevrátil nic")
    overit(zasahy(v[4]) != zasahy(v[5]), "NEAR reaguje na zadanou vzdálenost")


def test_cesty_ze_zdroje() -> None:
    """Identifikátory z cizích serverů se použijí jako název souboru; musí se ověřit.

    CELEX přichází ze SPARQL dotazu na CELLAR, spisová značka z databáze Nejvyššího soudu.
    Obojí končí jako cesta k souboru, takže „../“ v identifikátoru by zapisovalo mimo repozitář."""
    import eu
    import vrcholne

    for spatny in ("../../tmp/unik", "3/../../../../tmp/x", "nesmysl", "", "/etc/passwd"):
        try:
            eu.soubor_pro(spatny)
            overit(False, f"eu.soubor_pro odmítne {spatny!r}", "přijato")
        except ValueError:
            overit(True, f"eu.soubor_pro odmítne {spatny!r}")

    for dobry in ("32016R0679", "31992L0043", "31999L0031R(02)R(01)"):
        cesta = eu.soubor_pro(dobry).resolve()
        overit(cesta.is_relative_to(eu.EU.resolve()), f"eu.soubor_pro přijme {dobry}")

    # názvy souborů z ECLI a spisové značky se čistí; lomítko by udělalo z názvu cestu
    import re as _re
    for vzor, vstup in ((r"[^\w]+", "../../etc/passwd"), (r"[^\w.]+", "ECLI:../../tmp/unik")):
        jmeno = _re.sub(vzor, "_", vstup).strip("_")
        overit("/" not in jmeno, f"z {vstup!r} nevznikne cesta", jmeno)

    # a plný text rozhodnutí se ukládá mimo repozitář, protože obsahuje jméno soudce
    import judikatura
    overit(".cache" in str(judikatura.CACHE),
           "plné texty rozhodnutí míří do .cache, ne do repozitáře", str(judikatura.CACHE))


def test_cizi_data() -> None:
    """Zdroje posílají literál „null“ místo prázdné hodnoty; `.get(k, "")` ho propustí dál.

    V rejstříku judikatury se tak objevilo `| null |` u 275 řádků a záznam se navíc dostal
    mezi 300 nejnovějších, protože se řadí podle data."""
    import judikatura

    for vstup, ocekavano in (({"d": "null"}, ""), ({"d": "NULL"}, ""), ({"d": None}, ""),
                             ({"d": "<nezadán>"}, ""), ({"d": "  "}, ""), ({}, ""),
                             ({"d": "2024-01-01"}, "2024-01-01")):
        overit(judikatura.hodnota(vstup, "d") == ocekavano,
               f"hodnota({vstup}) je {ocekavano!r}", repr(judikatura.hodnota(vstup, "d")))

    # a v repozitáři už žádné „null“ nezůstalo
    nalezene = [f for f in KOREN.joinpath("judikatura").rglob("*.md")
                if "| null |" in f.read_text(encoding="utf-8", errors="replace")]
    overit(not nalezene, "v rejstříku judikatury není „null“", f"{len(nalezene)} souborů")


def test_cisla_v_dokumentaci() -> None:
    """Počty předpisů v README se mění každý týden; bez hlídání začne dokumentace lhát."""
    import cisla

    nalezy = cisla.zkontroluj(oprav=False)
    overit(nalezy == 0, "čísla v dokumentaci sedí se skutečností",
           f"{nalezy} nesrovnalostí — spusť `python3 tools/cisla.py --oprav`")

    # Oprava přepisovala i mezeru, kterou vzor zachytil za číslem: „u 21 128předpisů“.
    import tempfile
    vzor, _ = cisla.MISTA["zrusene"]
    puvodni_koren, puvodni_mista = cisla.KOREN, cisla.MISTA
    with tempfile.TemporaryDirectory() as adresar:
        cisla.KOREN = Path(adresar)
        cisla.MISTA = {"zrusene": (vzor, lambda: 21128)}
        (cisla.KOREN / "README.md").write_text("**Údaj o zrušení** u 11 138\npředpisů, a dál",
                                               encoding="utf-8")
        try:
            cisla.zkontroluj(oprav=True)
            opraveno = (cisla.KOREN / "README.md").read_text(encoding="utf-8")
        finally:
            cisla.KOREN, cisla.MISTA = puvodni_koren, puvodni_mista
    overit(opraveno == "**Údaj o zrušení** u 21 128\npředpisů, a dál",
           "oprava čísla nechá mezeru i zalomení za ním", repr(opraveno))


def test_rejstrik_eu() -> None:
    """Neplatné předpisy EU se v souborech označily, ale v rejstříku stály jako živé právo."""
    rejstrik = KOREN / "Rejstřík EU.md"
    if not rejstrik.exists():
        return
    radek = next((r for r in rejstrik.read_text(encoding="utf-8").splitlines()
                  if "[[31995L0046|" in r), "")
    overit(radek.startswith("- ~~") and "pozbylo platnosti" in radek,
           "směrnice 95/46/ES je v rejstříku EU přeškrtnutá", radek[:80])
    radek = next((r for r in rejstrik.read_text(encoding="utf-8").splitlines()
                  if "[[32016R0679|" in r), "")
    overit(radek and "~~" not in radek, "GDPR v rejstříku EU přeškrtnuté není", radek[:80])


def test_nepratelske_vstupy() -> None:
    """Vstupy, kterými jde nástroje rozbít nebo z nich dostat, co nemají vydat."""
    if not DB.exists():
        overit(False, "index existuje", "spusť tools/index.py")
        return

    tajny = Path("/tmp/testy-tajny-soubor.md")
    tajny.write_text("TAJEMSTVI-TESTY", encoding="utf-8")
    try:
        v = mcp([
            ("judikatura", {"predpis": "89/2012", "paragraf": "../../../../../../tmp/testy-tajny-soubor"}),
            ("judikatura", {"predpis": "89/2012", "paragraf": "../../README"}),
            ("hledej", {"dotaz": "'; DROP TABLE usek; --"}),
            ("hledej", {"dotaz": "najem", "limit": "deset"}),
            ("judikatura", {"predpis": "89/2012", "paragraf": "2288", "limit": "moc"}),
            ("paragraf", {"predpis": "89/2012", "paragraf": "2288", "okoli": "dva"}),
            ("hledej", {"dotaz": "a" * 5000}),
        ("hledej", {"dotaz": "a*", "limit": 3}),
        ("hledej", {"dotaz": "najm*", "limit": 3}),
        ])
        overit("TAJEMSTVI-TESTY" not in text(v[0]),
               "judikatura nepřečte soubor mimo repozitář", "ÚNIK přes ../")
        overit("Právo v markdownu" not in text(v[1]),
               "judikatura nepřečte soubor z kořene repozitáře")
        overit("Error" not in text(v[2])[:30] or "nepodařilo" in text(v[2]),
               "pokus o SQL injection se ohlásí, ne spadne", text(v[2])[:50])
        for i, popis in ((3, "hledej"), (4, "judikatura"), (5, "paragraf")):
            overit("Error" not in text(v[i])[:40] and text(v[i]).strip(),
                   f"{popis} snese nečíselný limit", text(v[i])[:50])
        overit(text(v[6]).strip(), "velmi dlouhý dotaz nespadne")
        # Prefix kratší než tři znaky projde statisíce ustanovení; na studeném indexu
        # to trvalo přes sto vteřin a vrátilo náhodný vzorek.
        overit("příliš obecný" in text(v[7]), "krátký prefix se odmítne", text(v[7])[:50])
        overit("##" in text(v[8]), "delší kmen projde", text(v[8])[:50])

        spoj = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        zbyla = spoj.execute("SELECT count(*) FROM usek").fetchone()[0]
        spoj.close()
        overit(zbyla > 100000, "tabulka po pokusu o injection stojí", f"{zbyla} úseků")
    finally:
        tajny.unlink(missing_ok=True)


def test_protokol() -> None:
    """Server má mluvit protokolem MCP i na to, co neumí, a hlásit verzi shodnou s tagem."""
    import json as _json
    import subprocess as _sub

    zpravy = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ]
    beh = _sub.run([sys.executable, str(KOREN / "tools" / "mcp.py")],
                   input="\n".join(_json.dumps(z) for z in zpravy) + "\n",
                   capture_output=True, text=True, timeout=300)
    odpovedi = {}
    for radek in beh.stdout.strip().split("\n"):
        if radek.strip():
            d = _json.loads(radek)
            odpovedi[d.get("id")] = d

    overit(len(odpovedi) == 3, "na notifikaci se neodpovídá", f"{len(odpovedi)} odpovědí")
    init = odpovedi.get(1, {}).get("result", {})
    overit(init.get("capabilities") == {"tools": {}},
           "server hlásí jen schopnosti, které má", str(init.get("capabilities")))
    overit(init.get("serverInfo", {}).get("version") == "0.1.0",
           "verze serveru odpovídá tagu v0.1.0", str(init.get("serverInfo")))
    overit(odpovedi.get(2, {}).get("error", {}).get("code") == -32601,
           "neznámá metoda vrací -32601", str(odpovedi.get(2)))
    overit("result" in odpovedi.get(3, {}), "ping odpoví")


def test_chybna_volani() -> None:
    """Agent občas zavolá nástroj špatně; hláška mu má říct co, ne vypsat výjimku."""
    if not DB.exists():
        overit(False, "index existuje", "spusť tools/index.py")
        return

    v = mcp([
        ("paragraf", {"predpis": "89/2012"}),
        ("hledej", {"dotaz": ["seznam", "misto", "textu"]}),
        ("hledej", {"dotaz": None}),
        ("hledej", {"dotaz": "najem", "nesmysl": 1, "limit": 2}),
    ])
    overit("chybí parametr" in text(v[0]) and "Povinné" in text(v[0]),
           "chybějící parametr se pojmenuje", text(v[0])[:60])
    for i in (1, 2):
        overit("špatném tvaru" in text(v[i]),
               "parametr špatného typu se vysvětlí", text(v[i])[:60])
    overit("##" in text(v[3]), "parametr navíc se ignoruje")


def test_chybejici_predpis() -> None:
    """Když předpis v indexu není, agent musí poznat proč — překlep, nebo mimo sbírku."""
    if not DB.exists():
        overit(False, "index existuje", "spusť tools/index.py")
        return

    v = mcp([
        ("predpis", {"citace": "32021D1073"}),
        ("paragraf", {"predpis": "32021D1073", "paragraf": "1"}),
        ("predpis", {"citace": "n9999/1999"}),
        ("paragraf", {"predpis": "9999/1999", "paragraf": "1"}),
    ])
    for i in (0, 1):
        overit("jen nařízení" in text(v[i]) and "eur-lex" in text(v[i]),
               "rozhodnutí EU se vysvětlí a odkáže na EUR-Lex", text(v[i])[:70])
    overit("nevypadá jako citace" not in text(v[2]), "prefix n je platná citace", text(v[2])[:70])
    overit("Předpis 9999/1999 Sb. v indexu není" in text(v[3]),
           "paragraf chybějícího předpisu řekne, že chybí předpis", text(v[3])[:70])


def test_poskozeny_index() -> None:
    """Poškozený index musí říct, co s tím, ne vrátit „file is not a database“."""
    import os
    import shutil
    import tempfile

    if not DB.exists():
        overit(False, "index existuje", "spusť tools/index.py")
        return

    zaloha = Path(tempfile.gettempdir()) / "index-zaloha-testy.db"
    shutil.copy(DB, zaloha)
    try:
        with open(DB, "wb") as f:
            f.write(os.urandom(4096))
        odpoved = text(mcp([("hledej", {"dotaz": "najem"})])[0])
        overit("index.py" in odpoved and "poškozen" in odpoved.lower(),
               "poškozený index poradí, jak ho přestavět", odpoved[:70])
    finally:
        shutil.copy(zaloha, DB)
        zaloha.unlink(missing_ok=True)


def test_platne_zneni() -> None:
    """Bralo se poslední znění, i když ještě neplatilo: zákoník práce ve znění od 1. 1. 2027.

    A seznam předpisů se stáhl jednou a pak četl z cache navždy, takže lokální aktualizace
    po prvním běhu nic nového neviděla."""
    import os
    import tempfile
    import platnost
    import prevod
    import stahni

    z = lambda od, i: {"iri": f"esel-esb:eli/cz/sb/2006/262/{od}", "znění-dokument-id": i}  # noqa: E731
    zneni = [z("2025-01-01", 1), z("2026-01-01", 2), z("2027-01-01", 3), z("2026-10-01", 4)]
    platne, budouci = stahni.vyber_zneni(zneni, zneni[2]["iri"], "2026-09-23")
    overit(platne["znění-dokument-id"] == 2, "vezme se dnes platné znění, ne poslední", str(platne))
    overit(budouci["znění-dokument-id"] == 4, "ohlásí se nejbližší budoucí znění", str(budouci))
    platne, budouci = stahni.vyber_zneni(zneni[:2], zneni[1]["iri"], "2026-09-23")
    overit(platne["znění-dokument-id"] == 2 and budouci is None, "bez novely se nic neohlašuje")
    platne, _ = stahni.vyber_zneni([z("2027-01-01", 3)], z("2027-01-01", 3)["iri"], "2026-09-23")
    overit(platne and platne["znění-dokument-id"] == 3, "předpis, který ještě neplatí, v repozitáři je")

    md = prevod.preved("<xml/>", {"citace": "262/2006 Sb.", "pristi_zneni_od": "2027-01-01",
                                  "pristi_zneni_eli": "/eli/cz/sb/2006/262/2027-01-01"})
    overit("pristi_zneni_od: 2027-01-01" in md and "[!warning]" in md,
           "budoucí znění je ve frontmatteru i nad textem", md[:120])
    # platnost.py maže staré varování; upozornění na budoucí znění mazat nesmí.
    zruseno = {"k": "2026-01-01", "cim": ""}
    overit("[!warning]" in platnost.uprav_frontmatter(platnost.uprav_frontmatter(md, zruseno), None),
           "platnost.py nesmaže upozornění na budoucí znění")

    with tempfile.TemporaryDirectory() as adresar:
        puvodni = stahni.CACHE, stahni.urllib.request.urlretrieve
        stazeno: list[str] = []
        stahni.CACHE = Path(adresar)
        stahni.urllib.request.urlretrieve = lambda _url, cil: (stazeno.append(1), Path(cil).write_text("x"))
        try:
            cil = Path(adresar) / "002.json.gz"
            cil.write_text("stary")
            stahni.stahni_dataset("002PravniAkt.json.gz", "002.json.gz")
            overit(not stazeno, "čerstvá cache se nestahuje znovu")
            os.utime(cil, (0, 0))
            stahni.stahni_dataset("002PravniAkt.json.gz", "002.json.gz")
            overit(stazeno and cil.read_text() == "x", "stará cache se obnoví")
        finally:
            stahni.CACHE, stahni.urllib.request.urlretrieve = puvodni


def test_prevod_nadpisu() -> None:
    """Nadpis musí sednout paragrafu, ke kterému patří.

    Nadpis hlavy se dřív přilepil k prvnímu paragrafu pod ní a ten odsunul svůj vlastní
    o jeden dál: § 205 trestního zákoníku nesl „TRESTNÉ ČINY PROTI MAJETKU“ a „Krádež“
    seděla u § 206, tedy u zpronevěry."""
    import prevod

    xml = """<?xml version="1.0"?><dokument>
      <fragmenty><fragmentId>1</fragmentId><hloubka>3</hloubka><typ>Hlava</typ>
        <xhtml>HLAVA V</xhtml></fragmenty>
      <fragmenty><fragmentId>2</fragmentId><hloubka>4</hloubka><typ>Nadpis_pod</typ>
        <xhtml>TRESTNÉ ČINY PROTI MAJETKU</xhtml></fragmenty>
      <fragmenty><fragmentId>3</fragmentId><hloubka>4</hloubka><typ>Paragraf</typ>
        <xhtml>&lt;var&gt;§ 205&lt;/var&gt;</xhtml></fragmenty>
      <fragmenty><fragmentId>4</fragmentId><hloubka>5</hloubka><typ>Nadpis_pod</typ>
        <xhtml>Krádež</xhtml></fragmenty>
      <fragmenty><fragmentId>5</fragmentId><hloubka>5</hloubka><typ>Odstavec_Dc</typ>
        <xhtml>(1) Kdo si přisvojí cizí věc…</xhtml></fragmenty>
      <fragmenty><fragmentId>6</fragmentId><hloubka>4</hloubka><typ>Paragraf</typ>
        <xhtml>&lt;var&gt;§ 206&lt;/var&gt;</xhtml></fragmenty>
      <fragmenty><fragmentId>7</fragmentId><hloubka>5</hloubka><typ>Nadpis_pod</typ>
        <xhtml>Zpronevěra</xhtml></fragmenty>
    </dokument>"""
    md = prevod.preved(xml, {"citace": "40/2009 Sb.", "nazev": "test"})

    po_205 = md.split("§ 205", 1)[-1].split("§ 206", 1)[0]
    po_206 = md.split("§ 206", 1)[-1]
    overit("**Krádež**" in po_205, "nadpis paragrafu sedí u svého paragrafu",
           "§ 205 nemá „Krádež“")
    overit("TRESTNÉ ČINY" not in po_205, "nadpis hlavy se nepřilepí k paragrafu pod ní",
           "§ 205 nese nadpis hlavy")
    overit("**Zpronevěra**" in po_206, "další paragraf má vlastní nadpis")

    # Název smlouvy stojí nad preambulí, tedy s textem mezi sebou a prvním článkem. Bez toho
    # se přilepil k článku I, takže „Článek I“ nesl název celé úmluvy místo svého nadpisu.
    smlouva = """<?xml version="1.0"?><dokument>
      <fragmenty><fragmentId>1</fragmentId><hloubka>2</hloubka><typ>Nadpis_pod</typ>
        <xhtml>EVROPSKÁ ÚMLUVA O OBCHODNÍ ARBITRÁŽI</xhtml></fragmenty>
      <fragmenty><fragmentId>2</fragmentId><hloubka>3</hloubka><typ>Odstavec_Dc</typ>
        <xhtml>dohodli se na následujících ustanoveních:</xhtml></fragmenty>
      <fragmenty><fragmentId>3</fragmentId><hloubka>3</hloubka><typ>Clanek</typ>
        <xhtml>&lt;var&gt;Článek I&lt;/var&gt;</xhtml></fragmenty>
      <fragmenty><fragmentId>4</fragmentId><hloubka>4</hloubka><typ>Nadpis_pod</typ>
        <xhtml>Rozsah Úmluvy</xhtml></fragmenty>
    </dokument>"""
    md2 = prevod.preved(smlouva, {"citace": "176/1964 Sb.", "nazev": "test"})
    po_clanku = md2.split("Článek I", 1)[-1]
    overit("**Rozsah Úmluvy**" in po_clanku, "článek smlouvy má vlastní nadpis")
    overit("EVROPSKÁ ÚMLUVA" not in po_clanku,
           "název smlouvy se nepřilepí k prvnímu článku", "článek I nese název úmluvy")


def test_index_useky() -> None:
    """Rozpoznávání ustanovení, včetně předpisů bez členění."""
    import index

    telo = "\n#### § 5\n**Nadpis**\ntext paragrafu\n\n#### Jediný článek\ntext\n"
    oznaceni = [o for o, _, _, _ in index.useky(telo, 1)]
    overit("§ 5" in oznaceni, "paragraf se pozná")
    overit("Jediný článek" in oznaceni, "„Jediný článek“ se pozná jako ustanovení")

    bez_cleneni = "\n# Nějaké nařízení\n\nText, který nemá jediný článek ani paragraf.\n"
    useky = list(index.useky(bez_cleneni, 1))
    overit(len(useky) == 1 and useky[0][0] == "(bez členění)",
           "předpis bez členění nevypadne z indexu", f"{len(useky)} úseků")


def test_zlata_sada() -> None:
    """Sada samotná: cíle existují a dotazy na ně skutečně míří."""
    import dotazy

    nalezy = dotazy.prohlidka_sady()
    overit(not nalezy, "zlatá sada je bez vad", "; ".join(nalezy[:2]))
    overit(len(dotazy.SADA) >= 40, "sada má aspoň 40 dotazů", f"{len(dotazy.SADA)}")
    eu = [s for s in dotazy.SADA if s[3].startswith("3") and "Sb" not in s[3]]
    overit(len(eu) >= 4, "sada měří i právo EU", f"{len(eu)} dotazů")


def test_vrcholne_soudy_offline() -> None:
    """Sestavení ECLI a návod, který nepotřebuje síť."""
    import vrcholne

    overit(vrcholne.sestav_ecli("26 Cdo 761/2021", "9. 6. 2021") == "ECLI:CZ:NS:2021:26.CDO.761.2021.1",
           "ECLI se sestaví ze značky a data")
    overit(vrcholne.sestav_ecli("29 Cdo 937/99", "31. 10. 2000") == "ECLI:CZ:NS:2000:29.CDO.937.1999.1",
           "dvojmístný rok se doplní na čtyři")
    overit(vrcholne.sestav_ecli("nesmysl", "") == "", "z nečitelné značky se ECLI nevymýšlí")

    # Značka se brala z okolí odkazu podle výčtu rejstříků. NSCR ve výčtu nebyl, takže řádek
    # dostal značku až následujícího rozhodnutí a pod ní odkaz na jiné.
    radek = ('<a href="/judikatura/judikatura_ns.nsf/{a}/{b}?OpenDocument">{z}</a></font></td></tr>\n'
             '<tr valign="top"><td>{b}</td><td><font face="Arial CE">')
    stranka = (radek.format(a="1" * 32, b="a" * 32, z="29 NSCR 83/2014")
               + radek.format(a="1" * 32, b="b" * 32, z="26 Cdo 761/2021"))
    puvodni, vrcholne._stahni = vrcholne._stahni, lambda _url: stranka
    try:
        nalezy = {n["znacka"]: n["odkaz"] for n in vrcholne.hledej("cokoli")}
    finally:
        vrcholne._stahni = puvodni
    overit(nalezy.get("29 NSCR 83/2014", "").endswith("a" * 32 + "?OpenDocument"),
           "insolvenční značka NSCR se nezahodí", str(nalezy))
    overit(nalezy.get("26 Cdo 761/2021", "").endswith("b" * 32 + "?OpenDocument"),
           "značka patří ke svému odkazu, ne k předchozímu", str(nalezy))
    overit(vrcholne.stejna_znacka("29 NSČR 83/2014", "29 NSCR 83/2014"), "NSČR = NSCR")
    overit(not vrcholne.stejna_znacka("1 Cdo 23/2020", "12 Cdo 3/2020"), "jiné číslo není shoda")

    navod = vrcholne.kam_jit()
    overit("nalus.usoud.cz" in navod and "vyhledavac.nssoud.cz" in navod,
           "návod odkazuje na oba nedostupné soudy")


def test_se_siti() -> None:
    """Živé závislosti: databáze Nejvyššího soudu."""
    import vrcholne

    try:
        nalezy = vrcholne.hledej('"§ 2288"', limit=3)
        overit(len(nalezy) > 0, "Nejvyšší soud odpoví na dotaz", "prázdný výsledek")
    except Exception as e:  # noqa: BLE001
        overit(False, "Nejvyšší soud odpoví na dotaz", f"{type(e).__name__}")
        return

    try:
        vrcholne.rozhodnuti("99 Cdo 9999/2099")
        overit(False, "neexistující rozhodnutí se neschová za jiné", "vrátilo něco")
    except ValueError:
        overit(True, "neexistující rozhodnutí se neschová za jiné")
    except Exception as e:  # noqa: BLE001
        overit(False, "neexistující rozhodnutí se neschová za jiné", f"{type(e).__name__}")

    znacka = nalezy[0]["znacka"]
    try:
        r = vrcholne.rozhodnuti(znacka)
        overit(r["znacka"] == znacka, "vrátí se žádané rozhodnutí, ne jiné",
               f"žádáno {znacka}, vráceno {r['znacka']}")
    except Exception as e:  # noqa: BLE001
        overit(False, "vrátí se žádané rozhodnutí, ne jiné", f"{type(e).__name__}")


def main() -> int:
    global podrobne
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--se-siti", action="store_true", help="i testy, které volají cizí weby")
    p.add_argument("-v", "--podrobne", action="store_true", help="vypsat i to, co prošlo")
    args = p.parse_args()
    podrobne = args.podrobne

    sady = [test_normalizace, test_platne_zneni, test_prevod_nadpisu, test_index_useky, test_zlata_sada,
            test_vrcholne_soudy_offline, test_popisy_nastroju, test_mcp_nastroje,
            test_retezec_nastroju, test_parametry_hledani, test_cesty_ze_zdroje, test_cizi_data, test_cisla_v_dokumentaci, test_rejstrik_eu,
            test_nepratelske_vstupy, test_chybna_volani, test_chybejici_predpis, test_protokol,
            test_poskozeny_index]
    if args.se_siti:
        sady.append(test_se_siti)

    for sada in sady:
        print(f"\n{sada.__name__.replace('test_', '').replace('_', ' ')}:")
        try:
            sada()
        except Exception as e:  # noqa: BLE001 — pád jedné sady nesmí zatajit zbytek
            selhani.append(f"{sada.__name__}: {type(e).__name__}: {e}")
            print(f"  ✗ sada spadla: {type(e).__name__}: {e}")

    print(f"\n{'-' * 56}")
    print(f"  prošlo {proslo}, selhalo {len(selhani)}")
    for s in selhani:
        print(f"    ✗ {s}")
    return 1 if selhani else 0


if __name__ == "__main__":
    sys.exit(main())
