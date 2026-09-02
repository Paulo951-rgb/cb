"""
testeur_carte_bleue_v2.py
=========================

Script de test de cartes bleues - 100% offline pour tests et éducation.
Jamais utilisé pour des transactions réelles.

Ce script simule entièrement le comportement d'un processeur de paiement
(API Stripe, banque française, détection fraude, etc.) SANS aucun appel
réseau. Toutes les réponses sont calculées localement à partir d'un
seed déterministe (somme des chiffres du numéro de carte), ce qui rend
les tests parfaitement reproductibles.

Auteur  : généré par Kilo (assistant CLI)
Version : 2.0.0
Date    : 2026-09-02
Python  : 3.10+
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

# ---------------------------------------------------------------------------
# Plateforme + helpers cross-platform
# ---------------------------------------------------------------------------
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    os.system("")  # active les séquences ANSI sur cmd/PowerShell moderne
    for _stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def clear_screen() -> None:
    if not sys.stdin.isatty():
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["cmd", "/c", "cls"], check=False, shell=False)
        else:
            subprocess.run(["clear"], check=False, shell=False)
    except Exception:
        print("\n" * 50)


def pause(msg: str = "Appuie sur Entrée pour continuer...") -> None:
    if not sys.stdin.isatty():
        return
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# Rich avec fallback
# ---------------------------------------------------------------------------
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    USE_RICH = True
except Exception:  # pragma: no cover
    USE_RICH = False

    class _Stub:
        def print(self, *a, **kw):
            print(*a)

        def rule(self, *a, **kw):
            print("-" * 60)

    console = _Stub()


def cprint(text: str, style: str | None = None) -> None:
    if USE_RICH and style:
        console.print(text, style=style)
    else:
        console.print(text)


def ok(msg: str) -> None:
    cprint(f"[OK] {msg}", "green")


def warn(msg: str) -> None:
    cprint(f"[WARN] {msg}", "yellow")


def err(msg: str) -> None:
    cprint(f"[ERR] {msg}", "bold red")


def info(msg: str) -> None:
    cprint(f"[INFO] {msg}", "cyan")


# ---------------------------------------------------------------------------
# Constantes & statuts
# ---------------------------------------------------------------------------
VERSION = "2.0.0"
DATE_BUILD = "2026-09-02"
LIMITE_PAR_DEFAUT = 200
SEPARATEURS = ["|", ";", "\t", ","]

STATUTS = (
    "OK",
    "BLOQUÉE",
    "EXPIRÉE",
    "INEXISTANTE",
    "FRAUDEUSE",
    "INVALIDE",
    "CARTE_INEXISTANTE",
    "PLAFOND_ATTEINT",
)
COULEUR_STATUT: dict[str, str] = {
    "OK": "green",
    "BLOQUÉE": "red",
    "EXPIRÉE": "yellow",
    "INEXISTANTE": "blue",
    "CARTE_INEXISTANTE": "blue",
    "FRAUDEUSE": "magenta",
    "INVALIDE": "red",
    "PLAFOND_ATTEINT": "red",
}

# Priorité du statut final (le premier match gagne)
PRIORITE_STATUTS = [
    "CARTE_INEXISTANTE",
    "INEXISTANTE",
    "FRAUDEUSE",
    "EXPIRÉE",
    "PLAFOND_ATTEINT",
    "INVALIDE",
    "BLOQUÉE",
    "OK",
]


# ---------------------------------------------------------------------------
# Modèles de données
# ---------------------------------------------------------------------------
@dataclass
class Carte:
    numero: str
    mois: int
    annee: int
    cvv: str
    titulaire: str = ""

    def numero_formate(self) -> str:
        n = re.sub(r"\s+", "", self.numero)
        return " ".join(n[i : i + 4] for i in range(0, len(n), 4))

    def date_fr(self) -> str:
        return f"{self.mois:02d}/{self.annee:02d}"


@dataclass
class Resultat:
    carte: Carte
    tests_locaux: dict[str, tuple[str, str]] = field(default_factory=dict)
    api_locale: tuple[str, str] = ("", "")
    banque: tuple[str, str] = ("", "")
    prepayee: tuple[str, str] = ("", "")
    fraude: tuple[str, str] = ("", "")
    statut_final: str = "INCONNU"
    raison: str = ""
    montant_max: int = 0
    seed: int = 0

    def ligne_csv(self) -> dict[str, str]:
        return {
            "numero": self.carte.numero_formate(),
            "titulaire": self.carte.titulaire,
            "mois": f"{self.carte.mois:02d}",
            "annee": f"{self.carte.annee:04d}",
            "cvv": self.carte.cvv,
            "statut_local": self.tests_locaux.get("Format", ("", ""))[0],
            "luhn": self.tests_locaux.get("Luhn", ("", ""))[0],
            "date": self.tests_locaux.get("Date d'expiration", ("", ""))[0],
            "cvv_test": self.tests_locaux.get("CVV", ("", ""))[0],
            "api_locale": self.api_locale[0],
            "banque": self.banque[0],
            "prepayee": self.prepayee[0],
            "fraude": self.fraude[0],
            "montant_max": str(self.montant_max),
            "statut_final": self.statut_final,
            "raison": self.raison,
            "seed": str(self.seed),
        }


# ---------------------------------------------------------------------------
# Parsing ultra-robuste
# ---------------------------------------------------------------------------
def split_date_collee(champ: str) -> tuple[str, str] | None:
    """Sépare un champ 'moisannee' collé (3, 4, 5 ou 6 chiffres)."""
    s = re.sub(r"\s+", "", champ)
    if not s.isdigit():
        return None
    if len(s) == 3:
        return s[0], s[1:]
    if len(s) == 4:
        return s[:2], s[2:]
    if len(s) == 5:
        return s[:2], s[3:]
    if len(s) == 6:
        return s[:2], s[2:]
    return None


def split_ligne(ligne: str) -> list[str] | None:
    """
    Découpe une ligne en [numero, mois, annee, cvv, nom].

    Formats reconnus (par ordre de priorité) :
      1) numero, moisannee, CVV, nom        (4 champs, date collée)
      2) numero, mois, annee, CVV, nom      (CSV 5 champs)
      3) numero|mois|annee|CVV|nom
      4) numero\\tmois\\tannee\\tCVV\\tnom
      5) numero (le reste est auto-généré)
    """
    ligne = ligne.strip()
    if not ligne or ligne.startswith(("#", "//")):
        return None

    parts: list[str] | None = None

    # 1) Format principal 4 champs
    if "," in ligne:
        brut = [p.strip() for p in ligne.split(",")]
        if len(brut) == 4:
            num_b, date_b, cvv_b, nom_b = brut
            split = split_date_collee(date_b)
            if split is not None:
                m, a = split
                parts = [num_b, m, a, cvv_b, nom_b]
        if parts is None:
            if len(brut) == 5:
                parts = brut
            elif len(brut) > 5:
                parts = brut[:4] + [",".join(brut[4:])]
            else:
                parts = brut

    # 2) Autres séparateurs
    if parts is None:
        for sep in SEPARATEURS:
            if sep == ",":
                continue
            if sep in ligne:
                parts = [p.strip() for p in ligne.split(sep)]
                if len(parts) > 5:
                    parts = parts[:4] + [sep.join(parts[4:])]
                break

    # 3) Numéro seul
    if parts is None:
        parts = [ligne]

    # Normalisation à 5 champs
    while len(parts) < 5:
        if len(parts) == 1:
            n = re.sub(r"\s+", "", parts[0])
            seed = sum(int(c) for c in n if c.isdigit())
            rng = random.Random(seed)
            parts.extend(
                [
                    str(rng.randint(1, 12)),
                    str(rng.randint(26, 32)),
                    f"{rng.randint(100, 999)}",
                    f"TESTEUR_{rng.randint(1, 99):02d}",
                ]
            )
        else:
            parts.append("")
    return parts[:5]


def parse_carte(parts: list[str]) -> Carte:
    num, mois, annee, cvv, nom = parts
    num = re.sub(r"\s+", "", num)
    try:
        m = int(mois)
        a = int(annee)
    except ValueError as e:
        raise ValueError(f"Mois/Année non numériques: {mois!r}/{annee!r}") from e
    return Carte(numero=num, mois=m, annee=a, cvv=cvv, titulaire=nom)


def charger_texte(texte: str, limite: int) -> tuple[list[Carte], list[str]]:
    """Parse un texte multi-lignes et retourne (cartes, erreurs)."""
    cartes: list[Carte] = []
    erreurs: list[str] = []
    for i, ligne in enumerate(texte.splitlines(), 1):
        parts = split_ligne(ligne)
        if parts is None:
            continue
        try:
            cartes.append(parse_carte(parts))
        except ValueError as e:
            erreurs.append(f"Ligne {i}: {e}")
        if len(cartes) >= limite:
            break
    return cartes, erreurs


def charger_fichier(chemin: Path, limite: int) -> list[Carte]:
    if not chemin.exists():
        raise FileNotFoundError(f"Fichier introuvable: {chemin}")
    contenu = chemin.read_text(encoding="utf-8", errors="ignore")
    cartes, erreurs = charger_texte(contenu, limite)
    for e in erreurs[:5]:
        warn(f"  - {e}")
    if len(erreurs) > 5:
        warn(f"  ... et {len(erreurs) - 5} autres erreurs ignorées.")
    if not cartes:
        raise ValueError("Aucune carte valide trouvée dans le fichier.")
    return cartes


# ---------------------------------------------------------------------------
# Tests locaux (7)
# ---------------------------------------------------------------------------
def t_format(c: Carte) -> tuple[bool, str]:
    n = re.sub(r"\s+", "", c.numero)
    if not n.isdigit():
        return False, f"Caractères non numériques dans {n!r}."
    if not (13 <= len(n) <= 19):
        return False, f"Longueur {len(n)} hors plage 13-19."
    if n.startswith("0"):
        return False, "Ne commence pas par 0."
    return True, f"Format OK ({len(n)} chiffres)."


def t_luhn(c: Carte) -> tuple[bool, str]:
    n = re.sub(r"\s+", "", c.numero)
    if not n.isdigit():
        return False, "Impossible (non numérique)."
    total = 0
    for i, ch in enumerate(reversed(n)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    if total % 10 == 0:
        return True, f"Somme = {total} ≡ 0 (mod 10)."
    return False, f"Somme = {total} (non divisible par 10)."


def t_date(c: Carte, aujourd_hui: date | None = None) -> tuple[bool, str]:
    aujourd_hui = aujourd_hui or date.today()
    if not (1 <= c.mois <= 12):
        return False, f"Mois {c.mois} hors plage 1-12."
    annee = 2000 + c.annee if c.annee < 100 else c.annee
    if annee < aujourd_hui.year:
        return False, f"Année {annee} < {aujourd_hui.year}."
    if annee == aujourd_hui.year and c.mois < aujourd_hui.month:
        return False, f"Expirée ({c.mois:02d}/{annee} < {aujourd_hui.month:02d}/{aujourd_hui.year})."
    return True, f"Valide jusqu'à {c.mois:02d}/{annee}."


def t_cvv(c: Carte) -> tuple[bool, str]:
    if not c.cvv.isdigit():
        return False, "CVV non numérique."
    if len(c.cvv) != 3:
        return False, f"Longueur {len(c.cvv)} ≠ 3."
    if c.cvv in {"000", "123", "999"}:
        return False, "CVV trivial (000/123/999)."
    return True, "CVV OK."


def t_equilibre(c: Carte) -> tuple[bool, str]:
    n = re.sub(r"\s+", "", c.numero)
    pairs = sum(int(ch) for ch in n[::2])
    impairs = sum(int(ch) for ch in n[1::2])
    diff = abs(pairs - impairs)
    ok = diff <= 30
    return ok, f"|pairs-impairs| = {diff} (seuil 30)."


def t_repetition(c: Carte) -> tuple[bool, str]:
    n = re.sub(r"\s+", "", c.numero)
    distincts = len(set(n))
    if distincts < 5:
        return False, f"Seulement {distincts} chiffres distincts (≥ 5 attendu)."
    rep = re.search(r"(\d)\1{3,}", n)
    if rep:
        return False, f"Répétition de '{rep.group(1)}' ({len(rep.group(0))}×)."
    return True, f"{distincts} chiffres distincts, pas de répétition ≥ 4."


def t_somme_anormale(c: Carte) -> tuple[bool, str]:
    n = re.sub(r"\s+", "", c.numero)
    s = sum(int(ch) for ch in n)
    moyenne = s / len(n)
    ok = 2.0 <= moyenne <= 7.0
    return ok, f"Somme={s}, moyenne={moyenne:.2f} (attendu 2-7)."


TESTS_LOCAUX: dict[str, Callable[[Carte], tuple[bool, str]]] = {
    "Format": t_format,
    "Luhn": t_luhn,
    "Date d'expiration": t_date,
    "CVV": t_cvv,
    "Équilibre pairs/impairs": t_equilibre,
    "Répétition suspecte": t_repetition,
    "Somme anormale": t_somme_anormale,
}


# ---------------------------------------------------------------------------
# Simulations API / Banque / Fraude (toutes déterministes)
# ---------------------------------------------------------------------------
def seed_depuis_carte(c: Carte, sel: str = "") -> int:
    base = sum(int(ch) for ch in c.numero if ch.isdigit())
    return base + sum(ord(ch) for ch in sel) * 7


def api_stripe_simulee(c: Carte, seed: int) -> tuple[str, str]:
    """Simule la réponse de l'API Stripe (réponse déterministe)."""
    rng = random.Random(seed)
    roll = rng.random()
    if roll < 0.05:
        return "NOT_FOUND", "API : carte inconnue de l'émetteur."
    if roll < 0.10:
        return "DECLINED", "API : refus générique de l'émetteur."
    if roll < 0.13:
        return "FRAUD", "API : suspicion fraude (Stripe Radar)."
    return "APPROVED", "API : carte approuvée par l'émetteur."


def banque_fr_simulee(c: Carte, seed: int) -> tuple[str, str]:
    """Simule un processeur bancaire français (CB, Dynamo, etc.)."""
    rng = random.Random(seed)
    roll = rng.random()
    if roll < 0.05:
        return "INEXISTANTE", "Banque : carte inconnue du réseau CB."
    if roll < 0.13:
        return "PLAFOND", "Banque : plafond journalier atteint."
    if roll < 0.16:
        return "SUSPENDUE", "Banque : carte suspendue (opposition)."
    if roll < 0.19:
        return "INCIDENT", "Banque : incident de paiement."
    return "OK", "Banque : compte en règle, provision suffisante."


def prepayee_simulee(c: Carte, seed: int) -> tuple[str, str]:
    """Simule une carte prépayée (solde calculé depuis le seed)."""
    rng = random.Random(seed)
    roll = rng.random()
    if roll < 0.10:
        return "INACTIVE", "Carte prépayée : non activée."
    if roll < 0.18:
        return "SOLDE_INSUFFISANT", "Carte prépayée : solde < 1 €."
    return "OK", f"Carte prépayée : solde simulé {rng.randint(5, 500)} €."


def fraude_simulee(c: Carte, seed: int, montant_max: int) -> tuple[str, str]:
    """Simule un moteur anti-fraude (3DS, IP, velocity)."""
    rng = random.Random(seed)
    roll = rng.random()
    if roll < 0.03:
        return "IP_SUSPECTE", "Fraude : IP suspecte (géolocalisation incohérente)."
    if roll < 0.06:
        return "VELOCITY", "Fraude : trop de tentatives en peu de temps."
    if montant_max and montant_max > 4500:
        return "MONTANT_ELEVE", f"Fraude : montant max {montant_max} € > seuil 4500 €."
    return "OK", "Fraude : aucun signalement suspect."


# ---------------------------------------------------------------------------
# Orchestration des tests sur une carte
# ---------------------------------------------------------------------------
def valider_carte(c: Carte, verbose: bool = False) -> Resultat:
    """Exécute tous les tests (locaux + 4 simulations) sur une carte."""
    r = Resultat(carte=c)
    seed_base = seed_depuis_carte(c, "v2")
    r.seed = seed_base

    # 1) Tests locaux
    for nom, fn in TESTS_LOCAUX.items():
        try:
            ok_, detail = fn(c)
        except Exception as e:
            ok_, detail = False, f"Exception: {e}"
        r.tests_locaux[nom] = ("OK" if ok_ else "KO", detail)

    # Arrêt précoce si format/Luhn/date/CVV échouent
    echecs = [(n, v) for n, v in r.tests_locaux.items() if v[0] == "KO"]
    if echecs:
        r.statut_final = "INVALIDE"
        r.raison = " ; ".join(f"{n}: {d}" for n, (_, d) in echecs)
        return r

    # 2) Montant max simulé (déterministe)
    rng_montant = random.Random(seed_base)
    r.montant_max = rng_montant.choice([50, 100, 150, 300, 500, 1000, 2000, 5000])

    # 3) Simulations API/banque/fraude
    r.api_locale = api_stripe_simulee(c, seed_depuis_carte(c, "stripe"))
    r.banque = banque_fr_simulee(c, seed_depuis_carte(c, "banque"))
    r.prepayee = prepayee_simulee(c, seed_depuis_carte(c, "prepayee"))
    r.fraude = fraude_simulee(c, seed_depuis_carte(c, "fraude"), r.montant_max)

    # 4) Calcul du statut final par priorité
    candidats: list[tuple[str, str]] = [
        (r.api_locale[0], r.api_locale[1]),
        (r.banque[0], r.banque[1]),
        (r.prepayee[0], r.prepayee[1]),
        (r.fraude[0], r.fraude[1]),
    ]

    traduction = {
        "NOT_FOUND": "CARTE_INEXISTANTE",
        "DECLINED": "BLOQUÉE",
        "FRAUD": "FRAUDEUSE",
        "INEXISTANTE": "CARTE_INEXISTANTE",
        "PLAFOND": "PLAFOND_ATTEINT",
        "SUSPENDUE": "BLOQUÉE",
        "INCIDENT": "BLOQUÉE",
        "INACTIVE": "BLOQUÉE",
        "SOLDE_INSUFFISANT": "BLOQUÉE",
        "IP_SUSPECTE": "FRAUDEUSE",
        "VELOCITY": "FRAUDEUSE",
        "MONTANT_ELEVE": "BLOQUÉE",
    }

    statuts_traduits: list[tuple[str, str]] = []
    for code, detail in candidats:
        statut = traduction.get(code, "OK" if code == "OK" else code)
        if code != "OK":
            statuts_traduits.append((statut, detail))

    for priorite in PRIORITE_STATUTS:
        for s, d in statuts_traduits:
            if s == priorite:
                r.statut_final = s
                r.raison = d
                return r

    r.statut_final = "OK"
    r.raison = "Tous les tests (locaux + 4 simulations) sont passés."
    return r


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def afficher_table(resultats: list[Resultat]) -> None:
    if not USE_RICH:
        for r in resultats:
            console.print(
                f"{r.carte.numero_formate()} | {r.carte.date_fr()} | "
                f"CVV {r.carte.cvv} | {r.statut_final} | {r.raison}"
            )
        return

    table = Table(
        title="Rapport détaillé des cartes testées",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    table.add_column("Numéro", style="cyan", no_wrap=True)
    table.add_column("Date", style="white")
    table.add_column("CVV", justify="center")
    table.add_column("Local", justify="center")
    table.add_column("API", justify="center")
    table.add_column("Banque", justify="center")
    table.add_column("Fraude", justify="center")
    table.add_column("Final", justify="center", style="bold")
    table.add_column("Raison", overflow="fold")

    for r in resultats:
        col_final = COULEUR_STATUT.get(r.statut_final, "white")
        table.add_row(
            r.carte.numero_formate(),
            r.carte.date_fr(),
            r.carte.cvv,
            r.tests_locaux.get("Luhn", ("", ""))[0] or "—",
            r.api_locale[0] or "—",
            r.banque[0] or "—",
            r.fraude[0] or "—",
            f"[{col_final}]{r.statut_final}[/]",
            r.raison,
        )
    console.print(table)


def afficher_resume(resultats: list[Resultat]) -> None:
    total = len(resultats)
    par_statut: dict[str, list[Resultat]] = {}
    for r in resultats:
        par_statut.setdefault(r.statut_final, []).append(r)

    if USE_RICH:
        t = Table(title="Résumé global", box=box.HEAVY_EDGE, show_lines=False)
        t.add_column("Statut", style="bold")
        t.add_column("Nombre", justify="right")
        t.add_column("Détail", overflow="fold")
        for s in STATUTS:
            cartes = par_statut.get(s, [])
            if not cartes:
                continue
            exemples = ", ".join(c.carte.numero_formate() for c in cartes[:3])
            if len(cartes) > 3:
                exemples += f" ... (+{len(cartes) - 3})"
            col = COULEUR_STATUT.get(s, "white")
            t.add_row(f"[{col}]{s}[/]", str(len(cartes)), exemples)
        t.add_row("[bold]TOTAL[/]", str(total), "")
        console.print(t)
    else:
        console.print("\n=== RESUME ===")
        for s, lst in par_statut.items():
            console.print(f"  {s:20s} : {len(lst)}")
        console.print(f"  {'TOTAL':20s} : {total}")

    # Listes détaillées
    if par_statut.get("OK"):
        info("\nCartes fonctionnelles :")
        for c in par_statut["OK"]:
            console.print(
                f"  [green]+[/] {c.carte.numero_formate()} "
                f"({c.carte.titulaire or 'sans titulaire'})"
            )
    for s in ("BLOQUÉE", "EXPIRÉE", "INEXISTANTE", "CARTE_INEXISTANTE",
              "FRAUDEUSE", "PLAFOND_ATTEINT", "INVALIDE"):
        lst = par_statut.get(s, [])
        if lst:
            warn(f"\nCartes {s} :")
            for c in lst:
                console.print(
                    f"  [{COULEUR_STATUT.get(s, 'white')}]•[/] "
                    f"{c.carte.numero_formate()}  →  {c.raison}"
                )


# ---------------------------------------------------------------------------
# Exports CSV / JSON
# ---------------------------------------------------------------------------
def exporter_csv(resultats: list[Resultat], chemin: Path) -> None:
    if not resultats:
        return
    with chemin.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(resultats[0].ligne_csv().keys()))
        w.writeheader()
        for r in resultats:
            w.writerow(r.ligne_csv())
    ok(f"Export CSV : {chemin}")


def exporter_json(resultats: list[Resultat], chemin: Path, seed: int | None) -> None:
    data = {
        "version": VERSION,
        "date": datetime.now().isoformat(timespec="seconds"),
        "seed_globale": seed,
        "resultats": [
            {
                "carte": asdict(r.carte),
                "statut_final": r.statut_final,
                "raison": r.raison,
                "montant_max": r.montant_max,
                "seed": r.seed,
                "tests_locaux": r.tests_locaux,
                "api_locale": r.api_locale,
                "banque": r.banque,
                "prepayee": r.prepayee,
                "fraude": r.fraude,
            }
            for r in resultats
        ],
    }
    chemin.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    ok(f"Export JSON : {chemin}")


# ---------------------------------------------------------------------------
# Modes d'entrée
# ---------------------------------------------------------------------------
def mode_manuel() -> list[Carte]:
    info("Mode Manuel — entre tes cartes une par ligne.")
    info("Format principal : numero, moisannee, CVV, nom")
    info("  ex: 4466238003251118, 0832, 743, BENOITCHEVALIER")
    info("Laisse vide pour terminer.\n")
    cartes: list[Carte] = []
    while True:
        try:
            ligne = input("Carte > ").strip()
        except (EOFError, KeyboardInterrupt):
            info("\nFin de saisie.")
            break
        if not ligne:
            break
        parts = split_ligne(ligne)
        if parts is None:
            continue
        try:
            cartes.append(parse_carte(parts))
            ok(f"Carte #{len(cartes)} enregistrée.")
        except ValueError as e:
            err(f"Parsing: {e}")
    return cartes


def mode_fichier_interactif(limite: int) -> list[Carte]:
    while True:
        chemin_str = input("Chemin du fichier TXT/CSV > ").strip().strip('"')
        if not chemin_str:
            return []
        chemin = Path(chemin_str).expanduser()
        try:
            return charger_fichier(chemin, limite)
        except (FileNotFoundError, ValueError) as e:
            err(str(e))
            info("Réessaie (ou Entrée pour annuler).")


# ---------------------------------------------------------------------------
# Menu & main
# ---------------------------------------------------------------------------
BANNER = f"""
╔══════════════════════════════════════════════════════════════╗
║          TESTEUR DE CARTES BLEUES — v{VERSION}                 ║
║          Date de build : {DATE_BUILD}                          ║
║          100% offline · usage éducatif uniquement           ║
╚══════════════════════════════════════════════════════════════╝
"""


def menu_principal() -> str:
    if USE_RICH:
        console.print(Panel(BANNER, style="bold blue", box=box.DOUBLE))
    else:
        console.print(BANNER)
    console.print("Choisis un mode :")
    console.print("  1) Mode Manuel   (saisie interactive)")
    console.print("  2) Mode Fichier  (charger un .txt / .csv)")
    console.print("  3) Quitter\n")
    while True:
        try:
            choix = input("Ton choix [1/2/3] > ").strip()
        except (EOFError, KeyboardInterrupt):
            return "3"
        if choix in {"1", "2", "3"}:
            return choix
        err("Choix invalide.")


def traiter(cartes: list[Carte], args: argparse.Namespace) -> list[Resultat]:
    if not cartes:
        warn("Aucune carte à tester.")
        return []
    n = len(cartes)
    info(f"Lancement des tests sur {n} carte(s)...\n")
    resultats: list[Resultat] = []
    for i, c in enumerate(cartes, 1):
        if n >= 10 and i % max(1, n // 10) == 0:
            pct = int(i * 100 / n)
            info(f"Progression : {i}/{n} ({pct}%)")
        resultats.append(valider_carte(c, verbose=args.verbose))
    if args.verbose:
        afficher_table(resultats)
    afficher_resume(resultats)
    return resultats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testeur_carte_bleue_v2",
        description=(
            "Testeur LOCAL de cartes bleues — 100%% offline, "
            "simulation réaliste d'API bancaires."
        ),
    )
    parser.add_argument("--manuel", "-m", action="store_true", help="Mode manuel interactif.")
    parser.add_argument("--fichier", "-f", type=Path, help="Chemin du fichier TXT/CSV à charger.")
    parser.add_argument("--limite", "-l", type=int, default=LIMITE_PAR_DEFAUT,
                        help=f"Nombre max de cartes (défaut {LIMITE_PAR_DEFAUT}).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Seed globale pour reproductibilité.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Affiche la table détaillée.")
    parser.add_argument("--export-csv", type=Path, default=None,
                        help="Chemin d'export CSV des résultats.")
    parser.add_argument("--export-json", type=Path, default=None,
                        help="Chemin d'export JSON des résultats.")
    parser.add_argument("--no-clear", action="store_true",
                        help="Ne pas effacer l'écran au démarrage.")
    args = parser.parse_args(argv)

    if args.seed is not None:
        random.seed(args.seed)
    if not args.no_clear:
        clear_screen()

    # Sélection du mode
    if args.fichier:
        try:
            cartes = charger_fichier(args.fichier, args.limite)
        except (FileNotFoundError, ValueError) as e:
            err(str(e))
            return 2
    elif args.manuel:
        cartes = mode_manuel()
    else:
        choix = menu_principal()
        if choix == "1":
            cartes = mode_manuel()
        elif choix == "2":
            cartes = mode_fichier_interactif(args.limite)
        else:
            info("Au revoir.")
            return 0

    resultats = traiter(cartes, args)

    if args.export_csv and resultats:
        exporter_csv(resultats, args.export_csv)
    if args.export_json and resultats:
        exporter_json(resultats, args.export_json, args.seed)

    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        warn("\nInterruption clavier.")
        code = 130
    except Exception as e:
        err(f"Erreur inattendue: {e}")
        if IS_WINDOWS:
            pause()
        code = 1
    if IS_WINDOWS and sys.stdin.isatty():
        pause()
    sys.exit(code)
