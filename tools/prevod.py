"""Převádí XML jednoho znění právního předpisu z e-Sbírky na Obsidian markdown.

e-Sbírka vydá celý předpis jako plochý seznam fragmentů, kde každý nese typ (Paragraf, Odstavec_Dc,
Hlava…), hloubku zanoření a vlastní text v `<xhtml>`. Hloubka sama o sobě na úroveň nadpisu nestačí
— `Paragraf` se vyskytuje v hloubkách 4 až 7 podle toho, kolik částí a hlav je nad ním — takže
strukturu určuje typ a hloubka jen rozhoduje o zanoření seznamů.

Paragraf je vždy nadpis úrovně, na kterou se dá v Obsidianu odkázat: `[[89-2012#§ 2235]]`.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

FRAGMENT = re.compile(r"<fragmenty>(.*?)</fragmenty>", re.S)
POLE = {
    "id": re.compile(r"<fragmentId>(\d+)</fragmentId>"),
    "hloubka": re.compile(r"<hloubka>(\d+)</hloubka>"),
    "typ": re.compile(r"<typ>(.*?)</typ>"),
    "xhtml": re.compile(r"<xhtml>(.*?)</xhtml>", re.S),
}

# Strukturální úrovně předpisu a jejich úroveň nadpisu v markdownu.
UROVNE = {
    "Cast": 2,
    "Hlava": 3,
    "Dil": 4,
    "Oddil": 5,
    "Pododdil": 6,
}

# Paragraf sedí vždy o úroveň pod poslední strukturou nad ním, protože předpisy se liší v tom,
# kolik částí a hlav nad paragrafem je. Nadpis je z něj proto, aby šel adresovat: [[89-2012#§ 2235]].
CLANKY = {"Paragraf", "Clanek"}

# Fragmenty hlavičky dokumentu — skládají se do frontmatteru, ne do těla.
PREFIX = {
    "Prefix_Number": "cislo",
    "Prefix_Type": "druh",
    "Prefix_Date": "datum",
    "Prefix_Title": "nazev",
    "Prefix": "uvod",
}

NADPISY = {"Nadpis_nad", "Nadpis_pod", "Nadpis"}
SEZNAMY = {"Pismeno_Lb", "Bod_Dd"}


@dataclass
class Fragment:
    id: str
    hloubka: int
    typ: str
    text: str


def ocisti(surovy: str) -> str:
    """Z fragmentu udělá čistý markdown řádek."""
    t = html.unescape(surovy)
    # <var> nese označení (§ 12, písmeno a)) a v textu nemá vlastní význam
    t = re.sub(r"</?var>", "", t)
    t = re.sub(r"<br\s*/?>", " ", t)
    t = re.sub(r"</?(em|i)>", "*", t)
    t = re.sub(r"</?(strong|b)>", "**", t)
    t = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = unicodedata.normalize("NFC", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def nacti(xml: str) -> list[Fragment]:
    fragmenty = []
    for blok in FRAGMENT.findall(xml):
        hodnoty = {}
        for klic, vzor in POLE.items():
            m = vzor.search(blok)
            hodnoty[klic] = m.group(1) if m else ""
        fragmenty.append(
            Fragment(
                id=hodnoty["id"],
                hloubka=int(hodnoty["hloubka"] or 0),
                typ=hodnoty["typ"],
                text=ocisti(hodnoty["xhtml"]),
            )
        )
    return fragmenty


def preved(xml: str, meta: dict) -> str:
    """XML → markdown. `meta` doplní frontmatter o to, co v XML není (citace, ELI, datum znění)."""
    fragmenty = nacti(xml)
    hlavicka = {PREFIX[f.typ]: f.text for f in fragmenty if f.typ in PREFIX and f.text}

    telo: list[str] = []
    cekajici_nadpis: str | None = None
    struktura = 1  # úroveň poslední Části/Hlavy/Dílu, od které se odvíjí paragraf

    for f in fragmenty:
        if f.typ in PREFIX or not f.text and f.typ not in UROVNE and f.typ not in CLANKY:
            continue

        if f.typ in NADPISY:
            # Nadpis patří k označení, které přijde hned po něm (Nadpis_nad) nebo před ním.
            # V obou případech stojí na vlastním řádku, aby označení zůstalo čistou kotvou.
            # lstrip je nutný: označení se ukládá s "\n" na začátku, takže bez něj test nikdy
            # neprojde a nadpis hlavy se přilepí k prvnímu paragrafu pod ní.
            if telo and telo[-1].lstrip().startswith("#"):
                telo.append(f"**{f.text}**")
            else:
                cekajici_nadpis = f.text
            continue

        if f.typ in UROVNE or f.typ in CLANKY:
            if f.typ in UROVNE:
                uroven = UROVNE[f.typ]
                struktura = uroven
            else:
                uroven = min(struktura + 1, 6)
            popisek = f.text or f.typ
            # Nadpis jde pod označení, ne do něj: kotva [[89-2012#§ 1724]] musí sedět přesně,
            # a "§ 1724 — Obecná ustanovení" by ji rozbilo.
            telo.append(f"\n{'#' * uroven} {popisek}")
            if cekajici_nadpis:
                telo.append(f"**{cekajici_nadpis}**")
                cekajici_nadpis = None
            continue

        # Text mezi nadpisem a označením znamená, že nadpis k tomu označení nepatří: u smluv
        # stojí název celé úmluvy nad preambulí a bez tohohle se přilepil k článku I.
        #
        # Nadpis skupiny paragrafů („ÚVODNÍ USTANOVENÍ“ nad § 1 až 3) naopak text mezi sebou
        # nemá a zůstane u prvního paragrafu skupiny. XML pro takovou skupinu nemá vlastní
        # označení, takže víc se z něj vyčíst nedá; vyrobit umělou úroveň by rozbilo kotvy.
        if cekajici_nadpis:
            telo.append(f"**{cekajici_nadpis}**")
            cekajici_nadpis = None

        if f.typ in SEZNAMY:
            # hloubka 7 je základní úroveň seznamu, každá další se odsadí
            odsazeni = "  " * max(0, f.hloubka - 8)
            telo.append(f"{odsazeni}- {f.text}")
            continue

        telo.append(f.text)

    radky = ["---"]
    for klic in ("citace", "nazev", "druh", "datum", "ucinnost_od", "eli", "rok", "cislo",
                 "pristi_zneni_od", "pristi_zneni_eli"):
        hodnota = meta.get(klic) or hlavicka.get(klic)
        if hodnota:
            radky.append(f"{klic}: {json_str(hodnota)}")
    radky.append("tags:")
    # Mezinárodní smlouva není zákon — vlastní tag, ať se dá v Obsidianu odlišit.
    radky.append("  - smlouva" if meta.get("sbirka") == "sm" else "  - zakon")
    if meta.get("rok"):
        radky.append(f"  - rok/{meta['rok']}")
    radky.append("---")
    radky.append("")
    if meta.get("pristi_zneni_od"):
        # Nad textem, ne jen ve frontmatteru: kdo si vytáhne paragraf grepem, frontmatter nevidí.
        radky += [
            "> [!warning] Chystá se nové znění",
            f"> Tady je znění platné dnes. Od {meta['pristi_zneni_od']} platí nové, už vyhlášené: "
            f"https://e-sbirka.gov.cz{meta.get('pristi_zneni_eli', '')}",
            "",
        ]
    radky.append(f"# {meta.get('citace') or hlavicka.get('nazev', 'Předpis')}")
    if hlavicka.get("nazev"):
        radky.append("")
        radky.append(f"*{hlavicka['nazev']}*")
    if hlavicka.get("uvod"):
        radky.append("")
        radky.append(hlavicka["uvod"])

    vysledek = "\n".join(radky) + "\n" + "\n".join(telo) + "\n"
    return re.sub(r"\n{3,}", "\n\n", vysledek)


def json_str(hodnota: str) -> str:
    """YAML hodnota, uvozená jen když to potřebuje."""
    if re.search(r"[:#\[\]{}\"']|^\s|\s$", str(hodnota)):
        return '"' + str(hodnota).replace('"', '\\"') + '"'
    return str(hodnota)
