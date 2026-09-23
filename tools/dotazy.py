"""Změří, jestli agent na právní dotaz najde správné ustanovení.

`kontrola.py` ověřuje, že data drží pohromadě. Tohle je jiná otázka: **najde to, co má?**
Odpověď se nedá odhadnout, musí se změřit — sada dotazů, u kterých je správná odpověď známá
předem, a počítá se, na kolikátém místě skončila.

Každý dotaz je tu dvakrát: tak, jak by ho napsal člověk (`nájem bytu výpověď`), a tak, jak ho
musí napsat agent, který ví, že index nemá lemmatizaci (`najm* byt* vypove*`). Rozdíl mezi
těmi dvěma čísly je cena za chybějící lemmatizaci — a důvod, proč to stojí v popisu nástroje.

    python3 tools/dotazy.py            # projede sadu a vypíše, co kde skončilo
    python3 tools/dotazy.py --detail   # u každého dotazu vypíše, co server vrátil
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
MCP = KOREN / "tools" / "mcp.py"

# (co se hledá, jak by to napsal člověk, jak to napíše poučený agent, očekávaný předpis a §)
SADA = [
    ("výpověď nájmu bytu", "najem bytu vypoved", "vypove* najm* byt*", "89/2012 Sb.", "§ 2288"),
    ("jistota u nájmu", "jistota najem penezita", "jistot* najm* penez*", "89/2012 Sb.", "§ 2254"),
    ("promlčecí lhůta", "promlceci lhuta tri roky", "promlc* lhut*", "89/2012 Sb.", "§ 629"),
    ("náhrada újmy", "povinnost nahradit ujmu", "nahrad* ujm* povinn*", "89/2012 Sb.", "§ 2894"),
    ("rozvod manželství", "rozvod manzelstvi souziti", "rozved* manzelstv* souzit*", "89/2012 Sb.", "§ 755"),
    ("vydržení", "vydrzeni poctivy drzitel", "vydrz* poctiv* drzitel*", "89/2012 Sb.", "§ 1089"),
    ("zkušební doba", "zkusebni doba pracovni pomer", "zkusebn* dob* pracovn*", "262/2006 Sb.", "§ 35"),
    ("výpovědní doba", "vypovedni doba skonci pracovni pomer", "vypovedn* dob* pracovn*", "262/2006 Sb.", "§ 51"),
    ("dovolená", "dovolena za kalendarni rok", "dovolen* kalendarn* rok*", "262/2006 Sb.", "§ 211"),
    ("krádež", "kdo si prisvoji cizi vec zmocni", "prisvoj* ciz* vec*", "40/2009 Sb.", "§ 205"),
    ("odvolací lhůta", "odvolaci lhuta 15 dnu rozhodnuti", "odvolac* lhut* oznam*", "500/2004 Sb.", "§ 83"),
    ("náhrada nákladů řízení", "nahrada nakladu rizeni uspech", "nahrad* naklad* rizen* uspech*", "99/1963 Sb.", "§ 142"),
    ("sazby DPH", "sazba dane zdanitelne plneni", "sazb* dan* zdaniteln*", "235/2004 Sb.", "§ 47"),
    ("daň z příjmů", "sazba dane z prijmu fyzickych osob", "sazb* dan* prijm*", "586/1992 Sb.", "§ 16"),
    # Druhá vlna: napříč obory, ať sada neměří jen občanské právo. Dotazy jsou psané jako
    # právní otázka, ne opsané ze znění — jinak by se měřila jen přesná shoda slov.
    ("co je nájemní smlouva", "najemni smlouva pronajimatel prenechat vec k uzivani",
     "najemn* smlouv* prenech* uzivan*", "89/2012 Sb.", "§ 2201"),
    ("nepojmenovaná smlouva", "smlouva ktera neni upravena jako zvlastni typ",
     "smlouv* zahrnuj* upravujic* typ*", "89/2012 Sb.", "§ 1746"),
    ("bolestné", "bolestne ztizeni spolecenskeho uplatneni",
     "bolest* ztizen* spolecensk* uplatnen*", "89/2012 Sb.", "§ 2958"),
    ("škoda z porušení smlouvy", "nahrada skody poruseni smluvni povinnosti",
     "porus* povinn* smlouv* nahrad* skod*", "89/2012 Sb.", "§ 2913"),
    ("prodlení dlužníka", "dluznik neplni dluh radne a vcas prodleni",
     "dluzn* neplni* prodlen*", "89/2012 Sb.", "§ 1968"),
    ("výpovědní důvody", "z jakych duvodu muze zamestnavatel dat vypoved",
     "vypoved* duvod* zamestnavatel*", "262/2006 Sb.", "§ 52"),
    ("rozsah dohody o provedení práce", "dohoda o provedeni prace rozsah hodin",
     "dohod* proveden* prac* rozsah*", "262/2006 Sb.", "§ 75"),
    ("vrácení přeplatku mzdy", "vraceni nepravem vyplacenych castek zamestnanci",
     "vracen* nepravem* vyplacen* castk*", "262/2006 Sb.", "§ 331"),
    ("řízení pod vlivem", "ridil ve stavu vylucujicim zpusobilost alkohol",
     "stav* vylucujic* zpusobilost*", "40/2009 Sb.", "§ 274"),
    ("podvod", "uvede nekoho v omyl a obohati sebe nebo jineho",
     "obohat* omyl* skod* cizim majetku", "40/2009 Sb.", "§ 209"),
    ("zásady trestního řízení", "nikdo nemuze byt stihan jinak nez ze zakonnych duvodu",
     "stih* zakonn* duvod* zpusob*", "141/1961 Sb.", "§ 2"),
    ("náležitosti žaloby", "co musi obsahovat navrh na zahajeni rizeni",
     "navrh* zahajen* rizen* obsahov*", "99/1963 Sb.", "§ 79"),
    ("přípustnost dovolání", "kdy je dovolani pripustne proti rozhodnuti odvolaciho soudu",
     "dovolan* pripustn* odvolac*", "99/1963 Sb.", "§ 237"),
    ("lhůta pro rozhodnutí úřadu", "spravni organ vyda rozhodnuti bez zbytecneho odkladu",
     "vydat* rozhodnut* zbytecn* odklad*", "500/2004 Sb.", "§ 71"),
    ("uskutečnění zdanitelného plnění", "datum uskutecneni zdanitelneho plneni dodani zbozi",
     "uskutecn* zdaniteln* plnen* dodan*", "235/2004 Sb.", "§ 21"),
    ("příjmy ze závislé činnosti", "co jsou prijmy ze zavisle cinnosti",
     "prijm* zavisl* cinnost*", "586/1992 Sb.", "§ 6"),
    ("lhůta pro daňové přiznání", "dokdy se podava danove priznani za zdanovaci obdobi",
     "danov* priznan* zdanovac* obdob*", "280/2009 Sb.", "§ 136"),
    ("péče řádného hospodáře", "peclive a s potrebnymi znalostmi jedna ten kdo",
     "pecliv* potrebn* znalost* jedna*", "90/2012 Sb.", "§ 51"),
    ("doktorský studijní program", "doktorsky studijni program vedecke badani",
     "doktorsk* studijn* program*", "111/1998 Sb.", "§ 47"),
    ("podpora v nezaměstnanosti", "narok na podporu v nezamestnanosti uchazec",
     "narok* podpor* nezamestnanost* uchazec*", "435/2004 Sb.", "§ 39"),
    ("podpůrčí doba", "podpurci doba u nemocenskeho zacina",
     "podpurc* dob* nemocensk*", "187/2006 Sb.", "§ 26"),
    ("nárok na starobní důchod", "kdy ma pojistenec narok na starobni duchod",
     "narok* starobn* duchod* pojisten*", "155/1995 Sb.", "§ 29"),
    # Právo EU: platí v Česku přímo, takže se měří stejně jako české předpisy.
    ("zákonnost zpracování (GDPR)", "zpracovani osobnich udaju zakonne souhlas",
     "zpracovan* udaj* zakonn* souhlas*", "32016R0679", "Článek 6"),
    ("právo na výmaz", "pravo na vymaz subjekt udaju",
     "prav* vymaz* subjekt* udaj*", "32016R0679", "Článek 17"),
    # Pozor na dotazy, na které je věcně správná odpověď český předpis: „odstoupení od smlouvy
    # na dálku“ mířilo na směrnici 2011/83/EU, ale hledání správně vracelo § 1829 občanského
    # zákoníku, tedy její transpozici. Měřilo to formulaci testu, ne hledání. Dotazy na právo EU
    # proto míří tam, kde nařízení platí přímo a české obdoby nemá.
    ("odstoupení od smlouvy na dálku", "spotrebitel odstoupeni od smlouvy na dalku 14 dnu",
     "spotrebitel* odstoup* distancn*", "89/2012 Sb.", "§ 1829"),
    ("náhrada za zrušený let", "nahrada cestujicim za zruseny nebo zpozdeny let",
     "cestujic* nahrad* let*", "32004R0261", "Článek 7"),
    ("obecná příslušnost soudu v EU", "u soudu ktereho statu se zaluje osoba s bydlistem v EU",
     "zalovan* bydlist* clensk* stat* soud*", "32012R1215", "Článek 4"),
    ("hlášení úniku osobních údajů", "ohlaseni poruseni zabezpeceni osobnich udaju dozorovemu uradu",
     "porusen* zabezpecen* ohlas* dozorov*", "32016R0679", "Článek 33"),
]

LIMIT = 10


def zeptej_se(dotazy: list[str]) -> list[list[tuple[str, str]]]:
    """Pustí server jednou a projede jím všechny dotazy. Vrací zásahy jako (citace, označení)."""
    zpravy = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18"}}]
    for i, d in enumerate(dotazy, start=1):
        zpravy.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": "hledej", "arguments": {"dotaz": d, "limit": LIMIT}}})

    vstup = "\n".join(json.dumps(z, ensure_ascii=False) for z in zpravy) + "\n"
    beh = subprocess.run([sys.executable, str(MCP)], input=vstup,
                         capture_output=True, text=True, timeout=600)

    vysledky: dict[int, list[tuple[str, str]]] = {}
    for radek in beh.stdout.strip().split("\n"):
        if not radek:
            continue
        d = json.loads(radek)
        if not d.get("id"):
            continue
        text = d.get("result", {}).get("content", [{}])[0].get("text", "")
        zasahy = []
        for r in text.split("\n"):
            if r.startswith("## "):
                cast = r[3:].split("  ")[0].strip()
                # "89/2012 Sb. § 2288" -> ("89/2012 Sb.", "§ 2288")
                # Předpisy EU člení text na „Článek 6“, české na „§“ nebo „Čl.“.
                for znacka in (" §", " Článek ", " Čl."):
                    if znacka in cast:
                        citace, _, par = cast.partition(znacka)
                        zasahy.append((citace.strip(), znacka.strip() + " " + par.strip()
                                       if znacka != " §" else "§" + par.strip()))
                        break
        vysledky[d["id"]] = zasahy
    return [vysledky.get(i, []) for i in range(1, len(dotazy) + 1)]


def prohlidka_sady() -> list[str]:
    """Hledá vady v samotné sadě, ne v hledání.

    Sada je nástroj jako každý jiný a umí být rozbitá — cíl, který neexistuje, dotaz opsaný ze
    znění, nebo překlep ve slově s hvězdičkou, který tiše hledá něco jiného (`peciv*` místo
    `pecliv*` vracelo nařízení o účinnosti). Takové vady vypadají ve výsledku stejně jako
    špatné hledání, a tenhle přehled je od sebe odliší."""
    import sqlite3  # noqa: PLC0415 — potřeba jen tady

    db = KOREN / ".cache" / "index.db"
    if not db.exists():
        return ["index neexistuje, sadu nelze prohlédnout"]
    spoj = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    nalezy = []
    for popis, lidsky, agenti, predpis, par in SADA:
        cil = spoj.execute(
            "SELECT lower(text || ' ' || coalesce(nadpis,'') || ' ' || coalesce(predpis_nazev,'')) "
            "FROM usek WHERE predpis=? AND oznaceni=?", (predpis, par)).fetchone()
        if not cil:
            nalezy.append(f"{popis}: cíl {predpis} {par} v indexu není")
            continue
        # Slovo s hvězdičkou, jehož kmen se v cíli nevyskytuje, bývá překlep.
        for slovo in agenti.split():
            kmen = slovo.rstrip("*").lower()
            if len(kmen) >= 5 and slovo.endswith("*") and kmen not in _bez_diakritiky(cil[0]):
                nalezy.append(f"{popis}: „{slovo}“ se v cíli nevyskytuje — překlep?")
    spoj.close()
    return nalezy


def _bez_diakritiky(s: str) -> str:
    prevod = str.maketrans("áčďéěíňóřšťúůýž", "acdeeinorstuuyz")
    return s.translate(prevod)


def poradi(zasahy: list[tuple[str, str]], predpis: str, paragraf: str) -> int:
    """Kolikátý zásah je ten správný. 0 = není mezi nimi."""
    for i, (c, p) in enumerate(zasahy, start=1):
        if c == predpis and p.replace(" ", "") == paragraf.replace(" ", ""):
            return i
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--detail", action="store_true", help="vypsat, co server na každý dotaz vrátil")
    p.add_argument("--prohlidka", action="store_true", help="zkontrolovat sadu samotnou, neměřit")
    args = p.parse_args()

    if args.prohlidka:
        nalezy = prohlidka_sady()
        print(f"Prohlídka sady ({len(SADA)} dotazů): {len(nalezy) or 'bez'} nálezů")
        for n in nalezy:
            print(f"  ! {n}")
        return 1 if nalezy else 0

    naivni = zeptej_se([s[1] for s in SADA])
    poucene = zeptej_se([s[2] for s in SADA])

    print(f"{'co se hledá':<26} {'cíl':<22} {'člověk':>7} {'agent':>7}")
    print("-" * 66)
    skore = {"naivni": [0, 0, 0, 0], "poucene": [0, 0, 0, 0]}
    for (popis, dn, dp, predpis, par), zn, zp in zip(SADA, naivni, poucene):
        pn, pp = poradi(zn, predpis, par), poradi(zp, predpis, par)
        for klic, poz, zas in (("naivni", pn, zn), ("poucene", pp, zp)):
            if poz == 1:
                skore[klic][0] += 1
            if poz:
                skore[klic][1] += 1
            # Trefit správný předpis stačí: agent si v něm dohledá i sousední ustanovení.
            if zas and zas[0][0] == predpis:
                skore[klic][2] += 1
            # A úplně nejmírnější metrika: je správný předpis aspoň někde ve výsledcích?
            # Tohle je to, co agent reálně potřebuje — dostane zákon a v něm si dočte.
            if any(c == predpis for c, _ in zas):
                skore[klic][3] += 1
        znak = lambda x: f"{x}." if x else "—"  # noqa: E731
        print(f"{popis:<26} {predpis + ' ' + par:<22} {znak(pn):>7} {znak(pp):>7}")
        if args.detail:
            print(f"    člověk „{dn}“: {', '.join(f'{c} {p}' for c, p in zn[:5]) or 'nic'}")
            print(f"    agent  „{dp}“: {', '.join(f'{c} {p}' for c, p in zp[:5]) or 'nic'}")

    n = len(SADA)
    print("-" * 66)
    for klic, popis in (("naivni", "člověk (bez hvězdiček)"), ("poucene", "agent (s hvězdičkami)")):
        prvni, kdekoli, predp, predp_kde = skore[klic]
        print(f"  {popis:<24} ustanovení první {prvni}/{n} ({100*prvni//n} %), "
              f"v desítce {kdekoli}/{n} ({100*kdekoli//n} %), "
              f"správný předpis první {predp}/{n} ({100*predp//n} %), "
              f"ve výsledcích {predp_kde}/{n} ({100*predp_kde//n} %)")
    obojí = [skore["naivni"][i] + skore["poucene"][i] for i in range(4)]
    print(f"  {'dohromady':<24} ustanovení první {obojí[0]}/{2*n} ({100*obojí[0]//(2*n)} %), "
          f"v desítce {obojí[1]}/{2*n} ({100*obojí[1]//(2*n)} %), "
          f"správný předpis první {obojí[2]}/{2*n} ({100*obojí[2]//(2*n)} %), "
          f"ve výsledcích {obojí[3]}/{2*n} ({100*obojí[3]//(2*n)} %)")

    # Propadne, až když ani poučený dotaz nenajde nadpoloviční většinu — to už by znamenalo,
    # že je rozbité vyhledávání, ne jen formulace.
    return 0 if skore["poucene"][1] * 2 > n else 1


if __name__ == "__main__":
    sys.exit(main())
