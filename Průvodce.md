---
tags:
  - průvodce
---

# Průvodce pro právníka

Tenhle repozitář je **kompletní Sbírka zákonů a Sbírka mezinárodních smluv v textové podobě**,
stažená z [e-Sbírky](https://e-sbirka.gov.cz) a připravená tak, aby v ní šlo hledat — vám očima
i vašemu AI asistentovi strojově.

Není to náhrada ASPI ani Beck-online. Nemá komentáře, důvodové zprávy ani redakční anotace.
Má něco jiného: **celý text práva máte lokálně, zadarmo, a dá se v něm hledat strojově**.

## Co je uvnitř

| | |
|---|---|
| 31 151 předpisů | Sbírka zákonů od roku 1918 po dnešek |
| 2 263 smluv | Sbírka mezinárodních smluv |
| 328 582 ustanovení | každý paragraf a článek zvlášť dohledatelný |
| 10 459 zrušených | označených, aby se nespletly s platnými |
| 8 651 paragrafů s judikaturou | rozhodnutí soudů, která je vykládají |

Znění je **konsolidované**, tedy v platném znění včetně novel, ne jak bylo vyhlášeno. Novela, která už vyšla,
ale ještě neplatí, je ohlášená nad textem předpisu i s datem, od kdy platí.

## Jak se v tom hledá

### Otevřít v Obsidianu

Obsidian je čtečka na propojené poznámky, zdarma. Otevřete v ní tuhle složku jako „trezor"
(vault) a máte:

- **Rejstřík.md** — všechny předpisy po letech. Zrušené jsou ~~přeškrtnuté~~ a s datem.
- **Předpisy.base** — tabulka s filtry. Přepínač nahoře přepíná mezi pohledy
  „Platné předpisy", „Zrušené předpisy" a „Všechny".
- **Ctrl+O** otevře předpis podle názvu souboru: `89-2012` je občanský zákoník.
- **Ctrl+Shift+F** hledá fulltextově v celém právu.
- Kliknutím na odkaz `[[89-2012#§ 2235]]` skočíte rovnou na ten paragraf.

Soubory jsou obyčejný text, takže se dají otevřít i v poznámkovém bloku nebo Wordu.
Občanský zákoník najdete v `zakony/2012/89-2012.md`.

### Zeptat se AI asistenta

Tohle je ten hlavní důvod, proč repozitář vznikl. Asistent (Claude) si umí právo prohledat sám
a odpovědět s odkazem na konkrétní ustanovení, místo aby si ho vymyslel.

Zeptejte se normálně česky:

> Jaká je výpovědní doba u nájmu bytu na dobu neurčitou?

> Co říká zákoník práce o zkušební době a lze ji prodloužit?

> Najdi mi judikaturu k § 2235 občanského zákoníku.

Asistent hledá po jednotlivých ustanoveních, takže vám vrátí ten paragraf, ne celý kodex.
U každé odpovědi uvidíte citaci předpisu a číslo paragrafu — **vždy si to ověřte**, viz níže.

## Čemu nevěřit

Tohle si přečtěte, než z toho budete citovat.

**Znění je konsolidované, a tedy informativní.** Nejde o to, že by text v repozitáři
neodpovídal e-Sbírce — odpovídá jí přesně. Jde o to, že e-Sbírka vydává dvě různé věci:
*vyhlášené znění* (text tak, jak vyšel, to je právně závazné) a *konsolidované znění* (text se
zapracovanými novelami, které je ze zákona pouze informativní). Konsolidaci dělá Ministerstvo
vnitra jako službu, ale právní odpovědnost za ni nenese; kdyby se při zapracování novely spletlo,
platí to vyhlášené.

Repozitář obsahuje konsolidované znění, protože jen to má smysl číst. U čehokoli, co jde do
podání nebo smlouvy, si tedy formulaci ověřte proti vyhlášenému znění — odkaz ELI
na e-sbirka.gov.cz je v hlavičce každého předpisu.

**Předpis bez značky o zrušení e-Sbírka eviduje jako účinný.** Označeno je 10 459 zrušených
předpisů a zkontrolováno, že nechybí ani jeden, o kterém zdroj ví. Zbylých 22 959 má v e-Sbírce
evidované datum účinnosti a žádný konec.

Opatrnost je přesto na místě u velmi starých předpisů: formálně nezrušené nařízení z dvacátých
let e-Sbírka vede jako účinné, i když je fakticky obsoletní nebo překryté pozdější úpravou.
„Účinný" tu znamená „nebyl zrušen", ne „použije se na váš případ".

U každého paragrafu s judikaturou najdete seznam rozhodnutí se spisovou značkou a ECLI.
Plné texty v repozitáři nejsou — dohromady mají přes 13 GB — ale jednotlivé rozhodnutí
se stáhne na vyžádání a uloží čitelně:

```bash
python3 tools/judikatura.py --text ECLI:CZ:OSPH09:2026:15.C.241.2026.1
```

**Rejstřík judikatury je jen z nižších soudů.** Otevřená data justice.cz obsahují okresní,
krajské a vrchní soudy od října 2020.

**Nejvyšší soud** se přesto dohledat dá: jeho databáze odpovídá na strojové dotazy, takže se na
něj můžete zeptat asistenta („co říká Nejvyšší soud k § 2235") nebo použít příkaz:

```bash
python3 tools/vrcholne.py --paragraf 2235 --predpis 89/2012
python3 tools/vrcholne.py --text "26 Cdo 761/2021"
```

**Ústavní soud a Nejvyšší správní soud** strojově přístupné nejsou — jejich vyhledávače vyžadují
vyplněný formulář v prohlížeči. Asistent vám u nich řekne, kam jít a co tam zadat; hledá se
v [NALUSu](https://nalus.usoud.cz) a ve [vyhledávači NSS](https://vyhledavac.nssoud.cz).

**Chybí čtyři předpisy**, které e-Sbírka odmítá vydat kvůli rozsahu příloh: nařízení vlády
187/2018 Sb. o evropsky významných lokalitách s novelami 152/2022 a 113/2023, a smlouva
37/2002 Sb. m. s.

**Nejsou tu důvodové zprávy ani komentáře.**

**Právo EU tu naopak je** — 24 026 nařízení a směrnic česky. Ptejte se na ně stejně jako na
české předpisy („co říká GDPR o souhlasu se zpracováním"). Hledá se v nich hůř než v českém
právu: měřeno na zlaté sadě najde asi tři ze čtyř dotazů, protože český zákon na stejné téma
má obvykle stejná slova a bohatší judikaturu. Když hledáte konkrétní nařízení, pomůže dodat
jeho číslo.

## Jak přesné je hledání

Měřeno na sadě čtyřiceti běžných právních dotazů napříč obory, u kterých je správná odpověď
známá předem (`tools/dotazy.py`, dá se kdykoli spustit znovu):

| | |
|---|---|
| hledaný předpis mezi výsledky | 81 % |
| správný předpis na prvním místě | 70 % |
| správné ustanovení mezi prvními deseti | 67 % |
| přesně ten paragraf hned první | 42 % |

Zbytek jsou většinou případy, kdy hledání vrátí sousední paragraf téhož institutu — tedy
místo, odkud se k odpovědi dočtete.

Skloňování hledání zvládne, pokud je nainstalovaná lemmatizace (`pip install simplemma`, viz
README). Bez ní „bytu" nenajde „byt" a v Obsidianu je potřeba zkoušet kmen slova („nájem" i „nájm").

## Když se něco nezdá

Celý obsah se dá zkontrolovat jedním příkazem:

```bash
python3 tools/kontrola.py
```

Projde, jestli citace sedí se soubory, jestli jsou zrušené předpisy značené všude stejně,
jestli index pokrývá všechna data a jestli odkazy vedou tam, kam mají. Když něco nesedí,
vypíše co.

Aktualizace na nejnovější stav Sbírky je popsaná v [README](README.md).
