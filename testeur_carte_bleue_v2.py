"""
testeur_carte_bleue_v2.py
=========================

Testeur LOCAL de cartes bleues — 100% offline, à but éducatif uniquement.

Ce script simule intégralement le comportement d'un processeur de paiement
(API Stripe, banque française, moteur anti-fraude, cartes prépayées) SANS
aucun appel réseau. Toutes les réponses sont calculées localement à partir
d'un seed déterministe (somme des chiffres du numéro + sel), ce qui rend
les tests parfaitement reproductibles.

⚠️  Jamais utilisé pour des transactions réelles.

Auteur  : généré par Kilo (assistant CLI)
Version : 2.1.0
Date    : 2026-09-02
Python  : 3.10+
Licence : usage éducatif uniquement
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
from typing import Any, Callable, Iterable

# ===========================================================================
# Plateforme & helpers cross-platform
# ===========================================================================
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"

if IS_WINDOWS:
    # Active les séquences ANSI sur cmd/PowerShell moderne
    os.system("")
    for _stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def clear_screen() -> None:
    """Efface l'écran (Windows: cls, *nix: clear)."""
    if not sys.stdin.isatty():
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["cmd", "/c", "cls"], check=False)
        else:
            subprocess.run(["clear"], check=False)
    except Exception:
        print("\n" * 50)


def pause(msg: str = "Appuie sur Entrée pour continuer...") -> None:
    """Pause cross-platform (n'agit pas en mode non-interactif)."""
    if not sys.stdin.isatty():
        return
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        pass


# ===========================================================================
# Affichage : rich avec fallback ANSI propre
# ===========================================================================
USE_RICH = False
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table

    console = Console()
    USE_RICH = True
except Exception:
    # Fallback ANSI : couleurs 16 couleurs de base + bold
    class _Ansi:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RED = "\033[31m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        BLUE = "\033[34m"
        MAGENTA = "\033[35m"
        CYAN = "\033[36m"
        WHITE = "\033[37m"
        BRED = "\033[91m"
        BGREEN = "\033[92m"
        BYELLOW = "\033[93m"
        BBLUE = "\033[94m"
        BMAGENTA = "\033[95m"
        BCYAN = "\033[96m"

    _ansi = _Ansi()

    class _StubConsole:
        def print(self, *args: Any, **kwargs: Any) -> None:
            style = kwargs.get("style")
            text = " ".join(str(a) for a in args)
            if style:
                code = getattr(_ansi, style.upper().replace("bold ", "B").replace(" ", ""), "")
                if code:
                    text = f"{code}{text}{_ansi.RESET}"
            print(text)

        def rule(self, title: str = "", **kwargs: Any) -> None:
            line = "─" * 60
            if title:
                print(f"\n── {title} " + "─" * max(0, 56 - len(title)))
            else:
                print(f"\n{line}")

    console = _StubConsole()
    box = None  # non utilisé en fallback


# Suggère l'installation de rich si absent
if not USE_RICH:
    if sys.stdin.isatty() and "--no-rich-warn" not in sys.argv:
        print(
            "[INFO] Le module 'rich' n'est pas installé — utilisation d'un "
            "fallback ANSI.\n"
            "       Pour un rendu optimal: pip install rich\n",
            file=sys.stderr,
        )


def cprint(text: str, style: str | None = None) -> None:
    if USE_RICH and style:
        console.print(text, style=style)
    else:
        console.print(text, style=style)


def info(msg: str) -> None:
    cprint(f"[INFO] {msg}", "cyan")


def ok(msg: str) -> None:
    cprint(f"[OK] {msg}", "green")


def warn(msg: str) -> None:
    cprint(f"[WARN] {msg}", "yellow")


def err(msg: str) -> None:
    cprint(f"[ERR] {msg}", "bold red")


# ===========================================================================
# Constantes & métadonnées
# ===========================================================================
VERSION = "2.1.0"
DATE_BUILD = "2026-09-02"
LIMITE_PAR_DEFAUT = 200
SEUIL_COMPTEUR_TEMPS_REEL = 50

SEPARATEURS = ["|", ";", "\t"]

STATUTS_FINAUX = (
    "OK",
    "BLOQUÉE",
    "EXPIRÉE",
    "CARTE_INEXISTANTE",
    "INEXISTANTE",
    "FRAUDEUSE",
    "PLAFOND_ATTEINT",
    "INVALIDE",
)
COULEUR_STATUT: dict[str, str] = {
    "OK": "green",
    "BLOQUÉE": "red",
    "EXPIRÉE": "yellow",
    "INEXISTANTE": "blue",
    "CARTE_INEXISTANTE": "blue",
    "FRAUDEUSE": "magenta",
    "PLAFOND_ATTEINT": "red",
    "INVALIDE": "red",
}

# Priorité du statut final (premier match gagne)
PRIORITE_STATUTS: list[str] = [
    "CARTE_INEXISTANTE",
    "INEXISTANTE",
    "FRAUDEUSE",
    "EXPIRÉE",
    "PLAFOND_ATTEINT",
    "INVALIDE",
    "BLOQUÉE",
    "OK",
]

# Patterns bancaires connus (cartes de test classiques ou suspects)
PATTERNS_SUSPECTS = {
    "1111111111111111": "séquence de 1",
    "2222222222222222": "séquence de 2",
    "9999999999999999": "séquence de 9",
    "1234567890123456": "compteur linéaire",
    "4242424242424242": "carte de test Stripe",
    "5555555555554444": "carte de test Mastercard",
    "378282246310005": "carte de test Amex",
    "6011111111111117": "carte de test Discover",
    "0000000000000000": "carte nulle",
    "4111111111111111": "carte de test Visa classique",
    "4012888888881881": "carte de test Visa",
    "5105105105105100": "carte de test Mastercard",
    "4222222222222": "carte de test Visa 13 chiffres",
}

# ===========================================================================
# Modèles de données
# ===========================================================================
@dataclass
class Carte:
    """Représente une carte bleue en mémoire."""

    numero: str
    mois: int
    annee: int
    cvv: str
    titulaire: str = ""

    def numero_propre(self) -> str:
        return re.sub(r"\s+", "", self.numero)

    def numero_formate(self) -> str:
        n = self.numero_propre()
        return " ".join(n[i : i + 4] for i in range(0, len(n), 4))

    def date_fr(self) -> str:
        return f"{self.mois:02d}/{self.annee:02d}"


@dataclass
class Resultat:
    """Résultat complet d'une carte testée (local + 4 simulations)."""

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
    niveau_risque: str = "faible"  # faible | moyen | élevé | critique

    def ligne_csv(self) -> dict[str, str]:
        return {
            "numero": self.carte.numero_formate(),
            "titulaire": self.carte.titulaire,
            "mois": f"{self.carte.mois:02d}",
            "annee": f"{self.carte.annee:04d}",
            "cvv": self.carte.cvv,
            "format": self.tests_locaux.get("Format", ("", ""))[0],
            "luhn": self.tests_locaux.get("Luhn", ("", ""))[0],
            "date": self.tests_locaux.get("Date d'expiration", ("", ""))[0],
            "cvv_test": self.tests_locaux.get("CVV", ("", ""))[0],
            "caracteres": self.tests_locaux.get("Caractères autorisés", ("", ""))[0],
            "patterns": self.tests_locaux.get("Patterns suspects", ("", ""))[0],
            "equilibre": self.tests_locaux.get("Équilibre pairs/impairs", ("", ""))[0],
            "somme": self.tests_locaux.get("Somme anormale", ("", ""))[0],
            "api_locale": self.api_locale[0],
            "banque": self.banque[0],
            "prepayee": self.prepayee[0],
            "fraude": self.fraude[0],
            "montant_max": str(self.montant_max),
            "niveau_risque": self.niveau_risque,
            "statut_final": self.statut_final,
            "raison": self.raison,
            "seed": str(self.seed),
        }


# ===========================================================================
# Parsing ultra-robuste
# ===========================================================================
def split_date_collee(champ: str) -> tuple[str, str] | None:
    """Sépare 'moisannee' collé (3, 4, 5 ou 6 chiffres)."""
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
      1) numero, moisannee, CVV, nom            (4 champs, date collée)
      2) numero, mois, annee, CVV, nom          (CSV 5 champs)
      3) numero|mois|annee|CVV|nom
      4) numero\\tmois\\tannee\\tCVV\\tnom
      5) numero                                (auto-génération déterministe)
    """
    ligne = ligne.strip()
    if not ligne or ligne.startswith(("#", "//")):
        return None

    parts: list[str] | None = None

    # 1) CSV 4 champs (format principal)
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
                # nom avec virgules
                parts = brut[:4] + [",".join(brut[4:])]
            else:
                parts = brut

    # 2) Autres séparateurs
    if parts is None:
        for sep in SEPARATEURS:
            if sep in ligne:
                parts = [p.strip() for p in ligne.split(sep)]
                if len(parts) > 5:
                    parts = parts[:4] + [sep.join(parts[4:])]
                break

    # 3) Numéro seul
    if parts is None:
        parts = [ligne]

    # 4) Normalisation à 5 champs
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
    """Parse un texte multi-lignes → (cartes, erreurs)."""
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
    """Charge un fichier TXT/CSV (limite respectée)."""
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


# ===========================================================================
# Tests locaux (7 tests du prompt d'origine)
# ===========================================================================
def t_format(c: Carte) -> tuple[bool, str]:
    """Test 1 : format du numéro (longueur 13-19, que des chiffres)."""
    n = c.numero_propre()
    if not n.isdigit():
        return False, f"Caractères non numériques dans {n!r}."
    if not (13 <= len(n) <= 19):
        return False, f"Longueur {len(n)} hors plage 13-19."
    if n.startswith("0"):
        return False, "Ne commence jamais par 0."
    return True, f"Format OK ({len(n)} chiffres)."


def t_luhn(c: Carte) -> tuple[bool, str]:
    """Test 2 : algorithme de Luhn (somme ≡ 0 mod 10)."""
    n = c.numero_propre()
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
    """Test 3 : date d'expiration valide et non dépassée."""
    aujourd_hui = aujourd_hui or date.today()
    if not (1 <= c.mois <= 12):
        return False, f"Mois {c.mois} hors plage 1-12."
    annee = 2000 + c.annee if c.annee < 100 else c.annee
    if annee < aujourd_hui.year:
        return False, f"Année {annee} < {aujourd_hui.year}."
    if annee == aujourd_hui.year and c.mois < aujourd_hui.month:
        return False, (
            f"Expirée ({c.mois:02d}/{annee} < "
            f"{aujourd_hui.month:02d}/{aujourd_hui.year})."
        )
    return True, f"Valide jusqu'à {c.mois:02d}/{annee}."


def t_cvv(c: Carte) -> tuple[bool, str]:
    """Test 4 : CVV exactement 3 chiffres (4 pour Amex toléré)."""
    if not c.cvv.isdigit():
        return False, "CVV non numérique."
    if len(c.cvv) not in (3, 4):
        return False, f"Longueur {len(c.cvv)} (attendu 3 ou 4)."
    if c.cvv in {"000", "0000", "123", "1234", "999", "9999"}:
        return False, "CVV trivial (000/123/999)."
    return True, f"CVV OK ({len(c.cvv)} chiffres)."


def t_caracteres_autorises(c: Carte) -> tuple[bool, str]:
    """Test 5 : uniquement chiffres 0-9 (X/* acceptés en dernier pour Amex legacy)."""
    n = c.numero_propre()
    autorises = re.fullmatch(r"[0-9X*]+", n)
    if not autorises:
        return False, f"Caractères non autorisés (seuls 0-9, X, * sont valides)."
    # X et * seulement en dernière position
    for i, ch in enumerate(n):
        if ch in "X*" and i != len(n) - 1:
            return False, f"'{ch}' doit être en dernière position."
    return True, "Caractères autorisés (0-9, X/* terminal)."


def t_patterns_suspects(c: Carte) -> tuple[bool, str]:
    """Test 6 : patterns bancaires connus (cartes de test, séquences)."""
    n = c.numero_propre()
    if n in PATTERNS_SUSPECTS:
        return False, f"Pattern connu : {PATTERNS_SUSPECTS[n]}"
    # Séquences monotones (1111, 2222, etc.) ≥ 6 chiffres identiques
    rep = re.search(r"(\d)\1{5,}", n)
    if rep:
        return False, f"Répétition monotone de '{rep.group(1)}' ({len(rep.group(0))}×)."
    # Compteur linéaire strict 0123456789
    if n in {"0123456789012345", "1234567890123456", "9876543210987654"}:
        return False, "Compteur linéaire détecté."
    return True, "Aucun pattern suspect détecté."


def t_equilibre_pairs_impairs(c: Carte) -> tuple[bool, str]:
    """Test 7 : équilibre pairs/impairs (seuil strict ±20)."""
    n = c.numero_propre()
    pairs = sum(int(ch) for ch in n[::2])
    impairs = sum(int(ch) for ch in n[1::2])
    diff = abs(pairs - impairs)
    ok = diff <= 20
    return ok, f"|pairs-impairs| = {diff} (seuil strict 20)."


def t_somme_anormale(c: Carte) -> tuple[bool, str]:
    """Test bonus : somme moyenne des chiffres entre 2.5 et 6.5."""
    n = c.numero_propre()
    s = sum(int(ch) for ch in n)
    moyenne = s / len(n)
    ok = 2.5 <= moyenne <= 6.5
    return ok, f"Somme={s}, moyenne={moyenne:.2f} (attendu 2.5-6.5)."


TESTS_LOCAUX: dict[str, Callable[[Carte], tuple[bool, str]]] = {
    "Format": t_format,
    "Luhn": t_luhn,
    "Date d'expiration": t_date,
    "CVV": t_cvv,
    "Caractères autorisés": t_caracteres_autorises,
    "Patterns suspects": t_patterns_suspects,
    "Équilibre pairs/impairs": t_equilibre_pairs_impairs,
    "Somme anormale": t_somme_anormale,
}


# ===========================================================================
# Simulations API / Banque / Prépayée / Fraude
# ===========================================================================
def seed_depuis_carte(c: Carte, sel: str = "") -> int:
    """Seed déterministe = somme chiffres + sel hashé."""
    base = sum(int(ch) for ch in c.numero if ch.isdigit())
    return (base * 1_000_003 + sum(ord(ch) for ch in sel) * 7) & 0xFFFFFFFF


def api_stripe_simulee(c: Carte, seed: int) -> tuple[str, str]:
    """Simule une API Stripe-like."""
    rng = random.Random(seed)
    r = rng.random()
    # Cumuls : 0-5 NOT_FOUND, 5-10 DECLINED, 10-13 FRAUD, 13-15 EXPIRED,
    # 15-18 INSUFFICIENT_FUNDS, reste APPROVED
    if r < 0.05:
        return "NOT_FOUND", "API Stripe simulée : carte inconnue de l'émetteur."
    if r < 0.10:
        return "DECLINED", "API Stripe simulée : refus générique (do_not_honor)."
    if r < 0.13:
        return "FRAUD", "API Stripe simulée : blocage Radar (transaction_risk)."
    if r < 0.15:
        return "EXPIRED", "API Stripe simulée : carte expirée côté émetteur."
    if r < 0.18:
        return "INSUFFICIENT_FUNDS", "API Stripe simulée : provision insuffisante."
    return "APPROVED", "API Stripe simulée : transaction approuvée."


def banque_fr_simulee(c: Carte, seed: int) -> tuple[str, str]:
    """Simule un processeur bancaire français (CB / Dynamo)."""
    rng = random.Random(seed)
    r = rng.random()
    # 0-5 INEXISTANTE, 5-13 PLAFOND, 13-16 SUSPENDUE, 16-19 INCIDENT, 19-21 FRAUDE
    if r < 0.05:
        return "INEXISTANTE", "Banque FR : carte inconnue du réseau CB."
    if r < 0.13:
        return "PLAFOND", "Banque FR : plafond journalier dépassé."
    if r < 0.16:
        return "SUSPENDUE", "Banque FR : opposition enregistrée sur la carte."
    if r < 0.19:
        return "INCIDENT", "Banque FR : incident de paiement (réseau)."
    if r < 0.21:
        return "FRAUDE", "Banque FR : signalement fraude (Banque de France)."
    return "OK", "Banque FR : compte en règle, provision suffisante."


def prepayee_simulee(c: Carte, seed: int) -> tuple[str, str]:
    """Simule une carte prépayée (PCS, Transcash, etc.)."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.10:
        return "INACTIVE", "Carte prépayée : non activée (1ère recharge non faite)."
    if r < 0.18:
        return "SOLDE_INSUFFICIENT", "Carte prépayée : solde inférieur au montant."
    if r < 0.21:
        return "EXPIRED", "Carte prépayée : date de validité dépassée."
    return "OK", f"Carte prépayée : solde simulé {rng.randint(5, 500)} €."


def fraude_simulee(c: Carte, seed: int, montant_max: int) -> tuple[str, str]:
    """Simule un moteur anti-fraude (3DS, IP, velocity)."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.03:
        return "IP_SUSPECTE", "Fraude : IP géolocalisée dans un pays à risque."
    if r < 0.06:
        return "VELOCITY", "Fraude : trop de tentatives en peu de temps (velocity)."
    if r < 0.08:
        return "MONTANT_ELEVE", f"Fraude : montant {montant_max} € > seuil 4500 €."
    return "OK", "Fraude : aucun signalement suspect."


# ===========================================================================
# Orchestration
# ===========================================================================
TRADUCTION_SIMU: dict[str, str] = {
    "NOT_FOUND": "CARTE_INEXISTANTE",
    "DECLINED": "BLOQUÉE",
    "FRAUD": "FRAUDEUSE",
    "EXPIRED": "EXPIRÉE",
    "INSUFFICIENT_FUNDS": "PLAFOND_ATTEINT",
    "INEXISTANTE": "CARTE_INEXISTANTE",
    "PLAFOND": "PLAFOND_ATTEINT",
    "SUSPENDUE": "BLOQUÉE",
    "INCIDENT": "BLOQUÉE",
    "INACTIVE": "BLOQUÉE",
    "SOLDE_INSUFFICIENT": "PLAFOND_ATTEINT",
    "IP_SUSPECTE": "FRAUDEUSE",
    "VELOCITY": "FRAUDEUSE",
    "MONTANT_ELEVE": "BLOQUÉE",
}


def evaluer_niveau_risque(r: Resultat) -> str:
    """Évalue le niveau de risque (faible / moyen / élevé / critique)."""
    if r.statut_final in ("CARTE_INEXISTANTE", "INEXISTANTE", "INVALIDE"):
        return "critique"
    if r.statut_final == "OK":
        # Évalue selon les warnings
        warnings = sum(1 for v, _ in r.tests_locaux.values() if v == "WARN")
        if warnings == 0:
            return "faible"
        if warnings <= 2:
            return "moyen"
        return "élevé"
    if r.statut_final in ("FRAUDEUSE", "BLOQUÉE", "PLAFOND_ATTEINT"):
        return "élevé"
    if r.statut_final == "EXPIRÉE":
        return "moyen"
    return "moyen"


def valider_carte(c: Carte, verbose: bool = False) -> Resultat:
    """Exécute les 7 tests locaux + 4 simulations sur une carte."""
    r = Resultat(carte=c)
    r.seed = seed_depuis_carte(c, "v2.1")

    # 1) Tests locaux
    for nom, fn in TESTS_LOCAUX.items():
        try:
            ok_, detail = fn(c)
        except Exception as e:
            ok_, detail = False, f"Exception: {e}"
        statut = "OK" if ok_ else "KO"
        if not ok_ and nom in ("Patterns suspects", "Équilibre pairs/impairs", "Somme anormale"):
            statut = "WARN"  # Pas bloquant, juste suspect
        r.tests_locaux[nom] = (statut, detail)

    # Arrêt précoce si tests critiques échouent
    echecs_critiques = [
        (n, v) for n, v in r.tests_locaux.items()
        if v[0] == "KO" and n in ("Format", "Luhn", "Date d'expiration", "CVV", "Caractères autorisés")
    ]
    if echecs_critiques:
        r.statut_final = "INVALIDE"
        r.raison = " ; ".join(f"{n}: {d}" for n, (_, d) in echecs_critiques)
        r.niveau_risque = evaluer_niveau_risque(r)
        return r

    # 2) Montant max simulé
    rng_montant = random.Random(r.seed)
    r.montant_max = rng_montant.choice([50, 100, 150, 300, 500, 1000, 2000, 5000])

    # 3) Simulations
    r.api_locale = api_stripe_simulee(c, seed_depuis_carte(c, "stripe"))
    r.banque = banque_fr_simulee(c, seed_depuis_carte(c, "banque"))
    r.prepayee = prepayee_simulee(c, seed_depuis_carte(c, "prepayee"))
    r.fraude = fraude_simulee(c, seed_depuis_carte(c, "fraude"), r.montant_max)

    # 4) Statut final par priorité
    candidats: list[tuple[str, str]] = [
        r.api_locale,
        r.banque,
        r.prepayee,
        r.fraude,
    ]
    statuts_traduits: list[tuple[str, str]] = []
    for code, detail in candidats:
        statut_final = TRADUCTION_SIMU.get(code, "OK" if code in ("OK", "APPROVED") else code)
        if code not in ("OK", "APPROVED"):
            statuts_traduits.append((statut_final, detail))

    for priorite in PRIORITE_STATUTS:
        for s, d in statuts_traduits:
            if s == priorite:
                r.statut_final = s
                r.raison = d
                break
        if r.statut_final != "INCONNU":
            break
    else:
        r.statut_final = "OK"
        r.raison = "Tous les tests (locaux + 4 simulations) sont passés."

    r.niveau_risque = evaluer_niveau_risque(r)
    return r


# ===========================================================================
# Affichage
# ===========================================================================
def afficher_table_detaillee(resultats: list[Resultat]) -> None:
    """Table rich avec toutes les colonnes."""
    if not USE_RICH:
        # Fallback simple
        for r in resultats:
            col = COULEUR_STATUT.get(r.statut_final, "white")
            console.print(
                f"{r.carte.numero_formate()} | {r.carte.date_fr()} | "
                f"CVV {r.carte.cvv} | [{col}]{r.statut_final}[/] | "
                f"risque={r.niveau_risque} | {r.raison}",
                style=col,
            )
        return

    table = Table(
        title="Rapport détaillé des cartes testées",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        show_lines=False,
    )
    table.add_column("Numéro", style="cyan", no_wrap=True)
    table.add_column("Date", style="white")
    table.add_column("CVV", justify="center")
    table.add_column("Local", justify="center")
    table.add_column("API", justify="center")
    table.add_column("Banque", justify="center")
    table.add_column("Prépayée", justify="center")
    table.add_column("Fraude", justify="center")
    table.add_column("Risque", justify="center")
    table.add_column("Final", justify="center", style="bold")
    table.add_column("Raison", overflow="fold")

    for r in resultats:
        col_final = COULEUR_STATUT.get(r.statut_final, "white")
        local_ko = sum(1 for v, _ in r.tests_locaux.values() if v == "KO")
        local_warn = sum(1 for v, _ in r.tests_locaux.values() if v == "WARN")
        local_str = f"[green]OK[/]" if local_ko == 0 else f"[red]{local_ko}KO[/]"
        if local_warn:
            local_str += f"/[yellow]{local_warn}W[/]"

        risque_style = {
            "faible": "green",
            "moyen": "yellow",
            "élevé": "red",
            "critique": "bold red",
        }.get(r.niveau_risque, "white")

        table.add_row(
            r.carte.numero_formate(),
            r.carte.date_fr(),
            r.carte.cvv,
            local_str,
            r.api_locale[0] or "—",
            r.banque[0] or "—",
            r.prepayee[0] or "—",
            r.fraude[0] or "—",
            f"[{risque_style}]{r.niveau_risque}[/]",
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
        for s in STATUTS_FINAUX:
            lst = par_statut.get(s, [])
            if not lst:
                continue
            exemples = ", ".join(c.carte.numero_formate() for c in lst[:3])
            if len(lst) > 3:
                exemples += f" ... (+{len(lst) - 3})"
            col = COULEUR_STATUT.get(s, "white")
            t.add_row(f"[{col}]{s}[/]", str(len(lst)), exemples)
        t.add_row("[bold]TOTAL[/]", str(total), "")
        console.print(t)
    else:
        console.print("\n=== RESUME ===", style="bold")
        for s in STATUTS_FINAUX:
            lst = par_statut.get(s, [])
            if lst:
                console.print(f"  {s:20s} : {len(lst)}", style=COULEUR_STATUT.get(s))
        console.print(f"  {'TOTAL':20s} : {total}", style="bold")

    # Listes détaillées
    if par_statut.get("OK"):
        info("\nCartes fonctionnelles :")
        for c in par_statut["OK"]:
            console.print(
                f"  [green]+[/] {c.carte.numero_formate()} "
                f"({c.carte.titulaire or 'sans titulaire'}) — risque {c.niveau_risque}"
            )
    for s in (
        "BLOQUÉE",
        "EXPIRÉE",
        "INEXISTANTE",
        "CARTE_INEXISTANTE",
        "FRAUDEUSE",
        "PLAFOND_ATTEINT",
        "INVALIDE",
    ):
        lst = par_statut.get(s, [])
        if lst:
            warn(f"\nCartes {s} :")
            for c in lst:
                console.print(
                    f"  [{COULEUR_STATUT.get(s, 'white')}]•[/] "
                    f"{c.carte.numero_formate()}  →  {c.raison}"
                )


# ===========================================================================
# Exports
# ===========================================================================
def exporter_csv(resultats: list[Resultat], chemin: Path) -> None:
    if not resultats:
        return
    with chemin.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(resultats[0].ligne_csv().keys())
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in resultats:
            w.writerow(r.ligne_csv())
    ok(f"Export CSV : {chemin}")


def exporter_json(
    resultats: list[Resultat], chemin: Path, seed: int | None
) -> None:
    data = {
        "version": VERSION,
        "date": datetime.now().isoformat(timespec="seconds"),
        "seed_globale": seed,
        "total": len(resultats),
        "resultats": [
            {
                "carte": asdict(r.carte),
                "statut_final": r.statut_final,
                "raison": r.raison,
                "montant_max": r.montant_max,
                "seed": r.seed,
                "niveau_risque": r.niveau_risque,
                "tests_locaux": r.tests_locaux,
                "api_locale": r.api_locale,
                "banque": r.banque,
                "prepayee": r.prepayee,
                "fraude": r.fraude,
            }
            for r in resultats
        ],
    }
    chemin.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    ok(f"Export JSON : {chemin}")


# ===========================================================================
# Modes d'entrée interactifs
# ===========================================================================
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


# ===========================================================================
# Menu & main
# ===========================================================================
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
        console.print(BANNER, style="bold blue")
    console.print("Choisis un mode :", style="bold")
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


def traiter(
    cartes: list[Carte], args: argparse.Namespace
) -> list[Resultat]:
    if not cartes:
        warn("Aucune carte à tester.")
        return []
    n = len(cartes)
    info(f"Lancement des tests sur {n} carte(s)...\n")
    resultats: list[Resultat] = []

    if USE_RICH and n >= 10:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Tests en cours...", total=n)
            for c in cartes:
                resultats.append(valider_carte(c, verbose=args.verbose))
                progress.update(task, advance=1)
                # Compteur en temps réel si > SEUIL
                if n >= SEUIL_COMPTEUR_TEMPS_REEL:
                    i = len(resultats)
                    if i % max(1, n // 10) == 0:
                        pct = int(i * 100 / n)
                        info(f"  → {i}/{n} cartes traitées ({pct}%)")
    else:
        for i, c in enumerate(cartes, 1):
            resultats.append(valider_carte(c, verbose=args.verbose))
            if n >= SEUIL_COMPTEUR_TEMPS_REEL and i % max(1, n // 10) == 0:
                pct = int(i * 100 / n)
                info(f"  → {i}/{n} cartes traitées ({pct}%)")

    if args.verbose:
        afficher_table_detaillee(resultats)
    afficher_resume(resultats)
    return resultats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testeur_carte_bleue_v2",
        description=(
            "Testeur LOCAL de cartes bleues — 100% offline, simulation "
            "réaliste d'API bancaires (Stripe, banque FR, prépayée, fraude)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation :
  python testeur_carte_bleue_v2.py
  python testeur_carte_bleue_v2.py --fichier cartes.txt
  python testeur_carte_bleue_v2.py --fichier cartes.txt --seed 42 --verbose
  python testeur_carte_bleue_v2.py --fichier cartes.txt --export-csv out.csv --export-json out.json
  python testeur_carte_bleue_v2.py --manuel --limite 50

⚠️  Usage éducatif uniquement — aucune transaction réelle.
""",
    )
    parser.add_argument("--manuel", "-m", action="store_true", help="Mode manuel interactif.")
    parser.add_argument("--fichier", "-f", type=Path, help="Chemin du fichier TXT/CSV à charger.")
    parser.add_argument(
        "--limite", "-l", type=int, default=LIMITE_PAR_DEFAUT,
        help=f"Nombre max de cartes (défaut {LIMITE_PAR_DEFAUT}).",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Seed globale pour reproductibilité des tests.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Affiche la table détaillée avec tous les tests locaux.",
    )
    parser.add_argument(
        "--export-csv", type=Path, default=None,
        help="Chemin d'export CSV des résultats complets.",
    )
    parser.add_argument(
        "--export-json", type=Path, default=None,
        help="Chemin d'export JSON (avec seed globale).",
    )
    parser.add_argument(
        "--no-clear", action="store_true",
        help="Ne pas effacer l'écran au démarrage.",
    )
    parser.add_argument(
        "--no-rich-warn", action="store_true",
        help="Masque le message suggérant d'installer rich.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION} ({DATE_BUILD})",
    )
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
