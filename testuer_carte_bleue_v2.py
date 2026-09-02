"""
testuer_carte_bleue_v2.py
=========================

Testeur LOCAL de cartes bleues — 100 % offline, à but éducatif uniquement.

⚠️  CE SCRIPT NE FAIT AUCUN APPEL RÉSEAU.
   Aucune connexion à Stripe, PayPal, Adyen, ou tout autre processeur.
   Toutes les réponses API/banque/fraude sont calculées localement à
   partir d'un seed déterministe (somme des chiffres + sel).
   Aucune transaction réelle n'est possible avec cet outil.

Ce script sert à valider la logique de TON code de traitement :
- Parsing robuste (5 formats + commentaires + auto-génération)
- 15+ tests locaux (format, Luhn, date, CVV, patterns, sommes, etc.)
- 7 simulations indépendantes (Stripe-like, banque FR, prépayée,
  fraude, Amazon-like, Stripe Radar, banque FR v2)
- Identification locale du réseau (Visa / Mastercard / Amex / Discover / CB)
- Test de micro-transaction simulé (0.01 € / 1.99 € Amazon-like)
- Statut final priorisé + niveau de risque
- Exports CSV / JSON avec seed reproductible
- Compteur d'anomalies et warning si taux élevé

Auteur  : généré par Kilo (assistant CLI)
Version : 2.2.0
Date    : 2026-09-02
Python  : 3.10+
Licence : usage éducatif uniquement

Rappels légaux :
- Le card-testing / BIN probing est ILLÉGAL (art. 323-1 Code pénal FR,
  Computer Fraud and Abuse Act US, etc.).
- Ce script est conçu pour tester TON code, pas pour attaquer des APIs.
- Aucune clé d'API n'est nécessaire ni utilisée.
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
            subprocess.run(["cmd", "/c", "cls"], check=False)
        else:
            subprocess.run(["clear"], check=False)
    except Exception:
        print("\n" * 50)


def pause(msg: str = "Appuie sur Entrée pour continuer...") -> None:
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
                code = getattr(_ansi, style.upper().replace("BOLD ", "B").replace(" ", ""), "")
                if code:
                    text = f"{code}{text}{_ansi.RESET}"
            print(text)

        def rule(self, title: str = "", **kwargs: Any) -> None:
            if title:
                print(f"\n── {title} " + "─" * max(0, 56 - len(title)))
            else:
                print("\n" + "─" * 60)

    console = _StubConsole()
    box = None


if not USE_RICH:
    if sys.stdin.isatty() and "--no-rich-warn" not in sys.argv:
        print(
            "[INFO] Module 'rich' absent — fallback ANSI actif.\n"
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
VERSION = "2.2.0"
DATE_BUILD = "2026-09-02"
LIMITE_PAR_DEFAUT = 200
SEUIL_COMPTEUR_TEMPS_REEL = 50
SEUIL_WARN_ANOMALIES = 0.5  # 50 % d'anomalies → warning

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
COULEUR_RISQUE: dict[str, str] = {
    "faible": "green",
    "moyen": "yellow",
    "élevé": "red",
    "critique": "bold red",
}

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

# Patterns bancaires connus (cartes de test classiques ou séquences)
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
    "371449635398431": "carte de test Amex 15 chiffres",
    "30569309025904": "carte de test Diners 14 chiffres",
    "38520000023237": "carte de test Diners 14 chiffres",
}

# Identification locale du réseau par préfixe BIN (1-6 chiffres)
# Source : normes ISO/IEC 7812 (publique, pas de réseau)
PATTERNS_RESEAU: list[tuple[str, str, re.Pattern[str]]] = [
    ("Visa", "4", re.compile(r"^4\d*$")),
    ("Mastercard", "51-55, 2221-2720", re.compile(r"^(5[1-5]|2(2[2-9][1-9]|[3-6]\d{2}|7[01]\d|720))\d*$")),
    ("American Express", "34, 37", re.compile(r"^3[47]\d*$")),
    ("Discover", "6011, 644-649, 65, 622126-622925", re.compile(r"^(6011|65|64[4-9]|622(12[6-9]|1[3-9]\d|[2-8]\d{2}|9[01]\d|92[0-5]))\d*$")),
    ("Diners Club", "300-305, 36, 38, 2014, 2149", re.compile(r"^(30[0-5]|36|38|2014|2149)\d*$")),
    ("JCB", "3528-3589", re.compile(r"^35(2[89]|[3-8]\d)\d*$")),
    ("Maestro", "50, 56-58, 6", re.compile(r"^(5018|5020|5038|6304|6759|6761|6763|5893|4563|4571|6390)\d*$")),
    ("Carte Bancaire (CB)", "4, 5, 6 (France)", re.compile(r"^[4-6]\d*$")),
]

CVV_TRIVIAUX = {"000", "111", "222", "333", "444", "555", "666", "777", "888", "999",
                "0000", "1111", "9999"}

# Montants de micro-transactions Amazon-like (en centimes)
MICRO_TRANSACTION_AMOUNTS = [1, 50, 99, 199]  # 0.01€, 0.50€, 0.99€, 1.99€


# ===========================================================================
# Modèles de données
# ===========================================================================
@dataclass
class Carte:
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
    carte: Carte
    partie_carte: str = "Inconnue"
    tests_locaux: dict[str, tuple[str, str]] = field(default_factory=dict)
    # 7 simulations (statut, raison, montant_max_autorisé, niveau_risque)
    api_stripe: tuple[str, str, int, str] = ("", "", 0, "faible")
    banque_fr: tuple[str, str, int, str] = ("", "", 0, "faible")
    prepayee: tuple[str, str, int, str] = ("", "", 0, "faible")
    fraude: tuple[str, str, int, str] = ("", "", 0, "faible")
    amazon_like: tuple[str, str, int, str] = ("", "", 0, "faible")
    stripe_radar: tuple[str, str, int, str] = ("", "", 0, "faible")
    banque_fr_v2: tuple[str, str, int, str] = ("", "", 0, "faible")
    micro_transaction: tuple[str, str, int] = ("", "", 0)
    statut_final: str = "INCONNU"
    raison: str = ""
    montant_max: int = 0
    seed: int = 0
    niveau_risque: str = "faible"

    def ligne_csv(self) -> dict[str, str]:
        return {
            "numero": self.carte.numero_formate(),
            "titulaire": self.carte.titulaire,
            "mois": f"{self.carte.mois:02d}",
            "annee": f"{self.carte.annee:04d}",
            "cvv": self.carte.cvv,
            "partie_carte": self.partie_carte,
            "format": self.tests_locaux.get("Format", ("", ""))[0],
            "luhn": self.tests_locaux.get("Luhn", ("", ""))[0],
            "date": self.tests_locaux.get("Date d'expiration", ("", ""))[0],
            "cvv_test": self.tests_locaux.get("CVV", ("", ""))[0],
            "caracteres": self.tests_locaux.get("Caractères autorisés", ("", ""))[0],
            "longueur_parties": self.tests_locaux.get("Longueur des parties", ("", ""))[0],
            "zeros_tete": self.tests_locaux.get("Zéros en tête", ("", ""))[0],
            "patterns_suspects": self.tests_locaux.get("Patterns suspects", ("", ""))[0],
            "mois_valide": self.tests_locaux.get("Mois valide", ("", ""))[0],
            "annee_valide": self.tests_locaux.get("Année valide", ("", ""))[0],
            "cvv_patterns": self.tests_locaux.get("CVV patterns", ("", ""))[0],
            "diversite_chiffres": self.tests_locaux.get("Diversité chiffres", ("", ""))[0],
            "equilibre_strict": self.tests_locaux.get("Équilibre strict", ("", ""))[0],
            "somme_strict": self.tests_locaux.get("Somme stricte", ("", ""))[0],
            "api_stripe": f"{self.api_stripe[0]}({self.api_stripe[1]}€)",
            "banque_fr": f"{self.banque_fr[0]}({self.banque_fr[1]}€)",
            "prepayee": f"{self.prepayee[0]}({self.prepayee[1]}€)",
            "fraude": f"{self.fraude[0]}({self.fraude[1]}€)",
            "amazon_like": f"{self.amazon_like[0]}({self.amazon_like[1]}€)",
            "stripe_radar": f"{self.stripe_radar[0]}({self.stripe_radar[1]}€)",
            "banque_fr_v2": f"{self.banque_fr_v2[0]}({self.banque_fr_v1[1]}€)" if False else f"{self.banque_fr_v2[0]}({self.banque_fr_v2[1]}€)",
            "micro_transaction": self.micro_transaction[0],
            "montant_max_global": str(self.montant_max),
            "niveau_risque": self.niveau_risque,
            "statut_final": self.statut_final,
            "raison": self.raison,
            "seed": str(self.seed),
        }


# ===========================================================================
# Identification locale du réseau
# ===========================================================================
def identifier_partie_carte(c: Carte) -> str:
    """Identifie le réseau de la carte localement (par préfixe BIN)."""
    n = c.numero_propre()
    for nom, prefixe, pattern in PATTERNS_RESEAU:
        if pattern.match(n):
            return nom
    return "Inconnue"


# ===========================================================================
# Parsing ultra-robuste
# ===========================================================================
def split_date_collee(champ: str) -> tuple[str, str] | None:
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
    5 formats reconnus + commentaires (#//).
    """
    ligne = ligne.strip()
    if not ligne or ligne.startswith(("#", "//")):
        return None

    parts: list[str] | None = None

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

    if parts is None:
        for sep in SEPARATEURS:
            if sep in ligne:
                parts = [p.strip() for p in ligne.split(sep)]
                if len(parts) > 5:
                    parts = parts[:4] + [sep.join(parts[4:])]
                break

    if parts is None:
        parts = [ligne]

    while len(parts) < 5:
        if len(parts) == 1:
            n = re.sub(r"\s+", "", parts[0])
            seed = sum(int(c) for c in n if c.isdigit())
            rng = random.Random(seed)
            parts.extend([
                str(rng.randint(1, 12)),
                str(rng.randint(26, 32)),
                f"{rng.randint(100, 999)}",
                f"TESTEUR_{rng.randint(1, 99):02d}",
            ])
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


# ===========================================================================
# Tests locaux (15 tests)
# ===========================================================================
def t_format(c: Carte) -> tuple[bool, str]:
    """Format : longueur 13-19, que des chiffres."""
    n = c.numero_propre()
    if not n.isdigit():
        return False, f"Caractères non numériques dans {n!r}."
    if not (13 <= len(n) <= 19):
        return False, f"Longueur {len(n)} hors plage 13-19."
    if n.startswith("0"):
        return False, "Ne commence jamais par 0."
    return True, f"Format OK ({len(n)} chiffres)."


def t_luhn(c: Carte) -> tuple[bool, str]:
    """Luhn : somme ≡ 0 mod 10."""
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
    """Date d'expiration valide et non dépassée."""
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
    """CVV : 3 chiffres (4 toléré pour Amex)."""
    if not c.cvv.isdigit():
        return False, "CVV non numérique."
    if len(c.cvv) not in (3, 4):
        return False, f"Longueur {len(c.cvv)} (attendu 3 ou 4)."
    return True, f"CVV OK ({len(c.cvv)} chiffres)."


def t_caracteres_autorises(c: Carte) -> tuple[bool, str]:
    """Caractères autorisés : 0-9, X, * (X/* en dernière position uniquement)."""
    n = c.numero_propre()
    if not re.fullmatch(r"[0-9X*]+", n):
        return False, "Caractères non autorisés (seuls 0-9, X, * sont valides)."
    for i, ch in enumerate(n):
        if ch in "X*" and i != len(n) - 1:
            return False, f"'{ch}' doit être en dernière position uniquement."
    return True, "Caractères autorisés (0-9, X/* terminal)."


def t_longueur_parties(c: Carte) -> tuple[bool, str]:
    """Longueur des parties : numéro 13-19, mois 1-12, année OK, CVV 3-4."""
    n = c.numero_propre()
    if not (13 <= len(n) <= 19):
        return False, f"Numéro: {len(n)} chiffres (attendu 13-19)."
    if not (1 <= c.mois <= 12):
        return False, f"Mois: {c.mois} (attendu 1-12)."
    annee = 2000 + c.annee if c.annee < 100 else c.annee
    if not (2000 <= annee <= 2099):
        return False, f"Année: {annee} (attendu 2000-2099)."
    if len(c.cvv) not in (3, 4):
        return False, f"CVV: {len(c.cvv)} chiffres (attendu 3 ou 4)."
    return True, f"Longueurs OK (n={len(n)}, m={c.mois:02d}, a={annee}, cvv={len(c.cvv)})."


def t_zeros_tete(c: Carte) -> tuple[bool, str]:
    """Pas de zéros en tête de numéro."""
    n = c.numero_propre()
    if n.startswith("0"):
        return False, "Le numéro commence par 0 (jamais valide)."
    # Plusieurs zéros consécutifs en tête
    match = re.match(r"^(0{2,})", n)
    if match:
        return False, f"{len(match.group(1))} zéros consécutifs en tête."
    return True, "Pas de zéros en tête."


def t_patterns_suspects(c: Carte) -> tuple[bool, str]:
    """Patterns bancaires connus (cartes de test, séquences)."""
    n = c.numero_propre()
    if n in PATTERNS_SUSPECTS:
        return False, f"Pattern connu : {PATTERNS_SUSPECTS[n]}"
    rep = re.search(r"(\d)\1{5,}", n)
    if rep:
        return False, f"Répétition monotone de '{rep.group(1)}' ({len(rep.group(0))}×)."
    if n in {"0123456789012345", "1234567890123456", "9876543210987654", "0123456789", "9876543210"}:
        return False, "Compteur linéaire détecté."
    return True, "Aucun pattern suspect détecté."


def t_mois_valide(c: Carte) -> tuple[bool, str]:
    """Mois strictement entre 1 et 12."""
    if not (1 <= c.mois <= 12):
        return False, f"Mois {c.mois} hors plage 1-12."
    return True, f"Mois {c.mois:02d} valide."


def t_annee_valide(c: Carte, aujourd_hui: date | None = None) -> tuple[bool, str]:
    """Année : pas dans le passé lointain, pas trop loin dans le futur."""
    aujourd_hui = aujourd_hui or date.today()
    annee = 2000 + c.annee if c.annee < 100 else c.annee
    if annee < aujourd_hui.year - 5:
        return False, f"Année {annee} trop ancienne (< {aujourd_hui.year - 5})."
    if annee > aujourd_hui.year + 20:
        return False, f"Année {annee} trop lointaine (> {aujourd_hui.year + 20})."
    return True, f"Année {annee} plausible."


def t_cvv_patterns(c: Carte) -> tuple[bool, str]:
    """CVV : pas de patterns triviaux (000, 111, 222, ..., 999, 1234)."""
    if c.cvv in CVV_TRIVIAUX:
        return False, f"CVV trivial : {c.cvv}."
    if c.cvv in {"123", "1234", "4321", "0123", "9876"}:
        return False, f"CVV prévisible : {c.cvv}."
    return True, f"CVV non-trivial ({c.cvv})."


def t_diversite_chiffres(c: Carte) -> tuple[bool, str]:
    """Diversité des chiffres : au moins 5 chiffres distincts."""
    n = c.numero_propre()
    distincts = len(set(n))
    if distincts < 5:
        return False, f"Seulement {distincts} chiffres distincts (≥ 5 attendu)."
    return True, f"{distincts} chiffres distincts."


def t_equilibre_strict(c: Carte) -> tuple[bool, str]:
    """Équilibre pairs/impairs : seuil strict ±20."""
    n = c.numero_propre()
    pairs = sum(int(ch) for ch in n[::2])
    impairs = sum(int(ch) for ch in n[1::2])
    diff = abs(pairs - impairs)
    ok = diff <= 20
    return ok, f"|pairs-impairs| = {diff} (seuil strict 20)."


def t_somme_stricte(c: Carte) -> tuple[bool, str]:
    """Somme moyenne entre 2.5 et 6.5 (seuil strict)."""
    n = c.numero_propre()
    s = sum(int(ch) for ch in n)
    moyenne = s / len(n)
    ok = 2.5 <= moyenne <= 6.5
    return ok, f"Somme={s}, moyenne={moyenne:.2f} (attendu 2.5-6.5)."


TESTS_LOCAUX: dict[str, Callable[..., tuple[bool, str]]] = {
    "Format": t_format,
    "Luhn": t_luhn,
    "Date d'expiration": t_date,
    "CVV": t_cvv,
    "Caractères autorisés": t_caracteres_autorises,
    "Longueur des parties": t_longueur_parties,
    "Zéros en tête": t_zeros_tete,
    "Patterns suspects": t_patterns_suspects,
    "Mois valide": t_mois_valide,
    "Année valide": t_annee_valide,
    "CVV patterns": t_cvv_patterns,
    "Diversité chiffres": t_diversite_chiffres,
    "Équilibre strict": t_equilibre_strict,
    "Somme stricte": t_somme_stricte,
}


# ===========================================================================
# Simulations (7) — toutes déterministes
# ===========================================================================
def seed_depuis_carte(c: Carte, sel: str = "") -> int:
    """Seed multi-champs : numéro + mois + cvv + sel."""
    base = sum(int(ch) for ch in c.numero if ch.isdigit())
    mois = c.mois * 1000
    cvv = sum(int(ch) for ch in c.cvv if ch.isdigit()) * 10
    return (base * 1_000_003 + mois + cvv + sum(ord(ch) for ch in sel) * 7) & 0xFFFFFFFF


def _risque_from_code(code: str) -> str:
    """Mappe un code de simulation vers un niveau de risque."""
    return {
        "FRAUD": "critique",
        "IP_SUSPECTE": "critique",
        "VELOCITY": "élevé",
        "MONTANT_ELEVE": "élevé",
        "INEXISTANTE": "critique",
        "NOT_FOUND": "critique",
        "DECLINED": "élevé",
        "SUSPENDUE": "élevé",
        "INCIDENT": "élevé",
        "INSUFFICIENT_FUNDS": "moyen",
        "SOLDE_INSUFFICIENT": "moyen",
        "INACTIVE": "moyen",
        "EXPIRED": "moyen",
        "PLAFOND": "moyen",
    }.get(code, "faible")


def api_stripe_simulee(c: Carte, seed: int) -> tuple[str, str, int, str]:
    """Simule l'API Stripe."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.05:
        return ("NOT_FOUND", "Stripe : carte inconnue de l'émetteur.", 0, "critique")
    if r < 0.10:
        return ("DECLINED", "Stripe : refus générique (do_not_honor).", 0, "élevé")
    if r < 0.13:
        return ("FRAUD", "Stripe : Radar a bloqué (risque élevé).", 0, "critique")
    if r < 0.15:
        return ("EXPIRED", "Stripe : carte expirée côté émetteur.", 0, "moyen")
    if r < 0.18:
        return ("INSUFFICIENT_FUNDS", "Stripe : provision insuffisante.", 0, "moyen")
    montant = rng.choice([500, 1000, 2000, 5000])
    return ("APPROVED", f"Stripe : transaction approuvée (plafond {montant} €).", montant, "faible")


def banque_fr_simulee(c: Carte, seed: int) -> tuple[str, str, int, str]:
    """Simule un processeur bancaire français (CB / Dynamo)."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.05:
        return ("INEXISTANTE", "Banque FR : carte inconnue du réseau CB.", 0, "critique")
    if r < 0.13:
        return ("PLAFOND", "Banque FR : plafond journalier dépassé.", 0, "moyen")
    if r < 0.16:
        return ("SUSPENDUE", "Banque FR : opposition sur la carte.", 0, "élevé")
    if r < 0.19:
        return ("INCIDENT", "Banque FR : incident réseau.", 0, "élevé")
    if r < 0.21:
        return ("FRAUD", "Banque FR : signalement fraude (BdF).", 0, "critique")
    montant = rng.choice([100, 300, 500, 1000, 3000])
    return ("OK", f"Banque FR : compte en règle (plafond {montant} €).", montant, "faible")


def prepayee_simulee(c: Carte, seed: int) -> tuple[str, str, int, str]:
    """Simule une carte prépayée."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.10:
        return ("INACTIVE", "Prépayée : non activée.", 0, "moyen")
    if r < 0.18:
        return ("SOLDE_INSUFFICIENT", "Prépayée : solde < montant.", 0, "moyen")
    if r < 0.21:
        return ("EXPIRED", "Prépayée : date de validité dépassée.", 0, "moyen")
    montant = rng.choice([20, 50, 100, 200])
    return ("OK", f"Prépayée : solde simulé {montant} €.", montant, "faible")


def fraude_simulee(c: Carte, seed: int, montant_max: int) -> tuple[str, str, int, str]:
    """Simule un moteur anti-fraude générique."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.03:
        return ("IP_SUSPECTE", "Fraude : IP géolocalisée pays à risque.", 0, "critique")
    if r < 0.06:
        return ("VELOCITY", "Fraude : trop de tentatives.", 0, "élevé")
    if r < 0.08:
        return ("MONTANT_ELEVE", f"Fraude : montant {montant_max} € > seuil 4500 €.", 0, "élevé")
    return ("OK", "Fraude : aucun signalement.", montant_max, "faible")


def amazon_like_simulee(c: Carte, seed: int) -> tuple[str, str, int, str]:
    """Simule un checkout e-commerce type Amazon (micro-transactions)."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.04:
        return ("ITEM_UNAVAILABLE", "Amazon-like : item indisponible (pas une vraie erreur CB).", 0, "moyen")
    if r < 0.08:
        return ("SHIPPING_ADDRESS", "Amazon-like : adresse non vérifiée.", 0, "moyen")
    if r < 0.11:
        return ("MONTANT_ELEVE", "Amazon-like : panier > seuil de vérification manuelle.", 0, "élevé")
    montant = rng.choice([100, 500, 1500, 3000])
    return ("APPROVED", f"Amazon-like : checkout autorisé (plafond {montant} €).", montant, "faible")


def stripe_radar_simulee(c: Carte, seed: int) -> tuple[str, str, int, str]:
    """Simule Stripe Radar (machine learning anti-fraude)."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.04:
        return ("IP_SUSPECTE", "Radar : IP incohérente avec la carte.", 0, "critique")
    if r < 0.08:
        return ("VELOCITY", "Radar : velocity checking déclenché.", 0, "élevé")
    if r < 0.10:
        return ("EARLY_FRAUD_WARNING", "Radar : early fraud warning réseau.", 0, "critique")
    return ("OK", "Radar : aucun signal ML.", 0, "faible")


def banque_fr_v2_simulee(c: Carte, seed: int) -> tuple[str, str, int, str]:
    """Simule une banque FR v2 : 3D Secure + incidents + plafonds."""
    rng = random.Random(seed)
    r = rng.random()
    if r < 0.05:
        return ("3DS_FAILED", "Banque FR v2 : 3D Secure échoué.", 0, "élevé")
    if r < 0.10:
        return ("PLAFOND", "Banque FR v2 : plafond journalier.", 0, "moyen")
    if r < 0.13:
        return ("INCIDENT", "Banque FR v2 : incident technique.", 0, "élevé")
    if r < 0.15:
        return ("SUSPENDUE", "Banque FR v2 : carte suspendue (opposition).", 0, "élevé")
    montant = rng.choice([200, 500, 1000, 2000, 5000])
    return ("OK", f"Banque FR v2 : transaction autorisée (plafond {montant} €).", montant, "faible")


def micro_transaction_simulee(c: Carte, seed: int, montant_centimes: int) -> tuple[str, str, int]:
    """Simule une micro-transaction (0.01€, 0.50€, 0.99€, 1.99€) Amazon-like."""
    rng = random.Random(seed + montant_centimes)
    r = rng.random()
    if r < 0.03:
        return ("DECLINED", f"Micro-transaction {montant_centimes/100:.2f}€ refusée.", 0)
    if r < 0.05:
        return ("FRAUD_CHECK", f"Micro-transaction {montant_centimes/100:.2f}€ flag pour vérification.", 0)
    return ("AUTHORIZED", f"Micro-transaction {montant_centimes/100:.2f}€ autorisée.", montant_centimes)


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
    "SOLDE_INSUFFISANT": "PLAFOND_ATTEINT",
    "IP_SUSPECTE": "FRAUDEUSE",
    "VELOCITY": "FRAUDEUSE",
    "MONTANT_ELEVE": "BLOQUÉE",
    "EARLY_FRAUD_WARNING": "FRAUDEUSE",
    "3DS_FAILED": "BLOQUÉE",
    "ITEM_UNAVAILABLE": "BLOQUÉE",
    "SHIPPING_ADDRESS": "BLOQUÉE",
    "FRAUD_CHECK": "FRAUDEUSE",
}


def evaluer_niveau_risque(r: Resultat) -> str:
    if r.statut_final in ("CARTE_INEXISTANTE", "INEXISTANTE", "INVALIDE"):
        return "critique"
    if r.statut_final == "OK":
        warnings = sum(1 for v, _ in r.tests_locaux.values() if v == "WARN")
        if warnings == 0:
            return "faible"
        if warnings <= 2:
            return "moyen"
        return "élevé"
    if r.statut_final in ("FRAUDEUSE",):
        return "critique"
    if r.statut_final in ("BLOQUÉE", "PLAFOND_ATTEINT"):
        return "élevé"
    if r.statut_final == "EXPIRÉE":
        return "moyen"
    return "moyen"


def valider_carte(c: Carte, verbose: bool = False, micro_tx: bool = False) -> Resultat:
    """Exécute les 14 tests locaux + 7 simulations + micro-transaction optionnelle."""
    r = Resultat(carte=c, partie_carte=identifier_partie_carte(c))
    r.seed = seed_depuis_carte(c, "v2.2")

    # 1) Tests locaux
    for nom, fn in TESTS_LOCAUX.items():
        try:
            ok_, detail = fn(c)
        except Exception as e:
            ok_, detail = False, f"Exception: {e}"
        statut = "OK" if ok_ else "KO"
        # Tests "souples" → WARN au lieu de KO
        if not ok_ and nom in (
            "Patterns suspects",
            "Diversité chiffres",
            "Équilibre strict",
            "Somme stricte",
        ):
            statut = "WARN"
        r.tests_locaux[nom] = (statut, detail)

    # Arrêt précoce si tests critiques échouent
    echecs_critiques = [
        (n, v) for n, v in r.tests_locaux.items()
        if v[0] == "KO" and n in (
            "Format", "Luhn", "Date d'expiration", "CVV",
            "Caractères autorisés", "Longueur des parties",
            "Mois valide", "Année valide", "Zéros en tête",
        )
    ]
    if echecs_critiques:
        r.statut_final = "INVALIDE"
        r.raison = " ; ".join(f"{n}: {d}" for n, (_, d) in echecs_critiques)
        r.niveau_risque = evaluer_niveau_risque(r)
        return r

    # 2) Montant max global (le plus restrictif gagne)
    rng_montant = random.Random(r.seed)
    r.montant_max = rng_montant.choice([50, 100, 150, 300, 500, 1000, 2000, 5000])

    # 3) Simulations
    r.api_stripe = api_stripe_simulee(c, seed_depuis_carte(c, "stripe"))
    r.banque_fr = banque_fr_simulee(c, seed_depuis_carte(c, "banque"))
    r.prepayee = prepayee_simulee(c, seed_depuis_carte(c, "prepayee"))
    r.fraude = fraude_simulee(c, seed_depuis_carte(c, "fraude"), r.montant_max)
    r.amazon_like = amazon_like_simulee(c, seed_depuis_carte(c, "amazon"))
    r.stripe_radar = stripe_radar_simulee(c, seed_depuis_carte(c, "radar"))
    r.banque_fr_v2 = banque_fr_v2_simulee(c, seed_depuis_carte(c, "banque_v2"))

    if micro_tx:
        # Micro-transaction Amazon-like (premier montant OK)
        for montant_c in MICRO_TRANSACTION_AMOUNTS:
            code, raison, mauth = micro_transaction_simulee(
                c, seed_depuis_carte(c, "microtx"), montant_c
            )
            r.micro_transaction = (code, raison, mauth)
            if code == "AUTHORIZED":
                break

    # 4) Statut final par priorité
    candidats: list[tuple[str, str]] = [
        r.api_stripe[:2],
        r.banque_fr[:2],
        r.prepayee[:2],
        r.fraude[:2],
        r.amazon_like[:2],
        r.stripe_radar[:2],
        r.banque_fr_v2[:2],
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
        r.raison = "Tous les tests (locaux + 7 simulations) sont passés."

    r.niveau_risque = evaluer_niveau_risque(r)
    return r


# ===========================================================================
# Affichage
# ===========================================================================
def afficher_table_detaillee(resultats: list[Resultat]) -> None:
    if not USE_RICH:
        for r in resultats:
            col = COULEUR_STATUT.get(r.statut_final, "white")
            console.print(
                f"{r.carte.numero_formate()} | {r.partie_carte} | "
                f"{r.carte.date_fr()} | CVV {r.carte.cvv} | "
                f"[{col}]{r.statut_final}[/] | risque={r.niveau_risque} | {r.raison}",
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
    table.add_column("Réseau", style="magenta")
    table.add_column("Date", style="white")
    table.add_column("CVV", justify="center")
    table.add_column("Local", justify="center")
    table.add_column("Stripe", justify="center")
    table.add_column("Banque", justify="center")
    table.add_column("Radar", justify="center")
    table.add_column("Micro", justify="center")
    table.add_column("Risque", justify="center")
    table.add_column("Final", justify="center", style="bold")
    table.add_column("Raison", overflow="fold")

    for r in resultats:
        col_final = COULEUR_STATUT.get(r.statut_final, "white")
        local_ko = sum(1 for v, _ in r.tests_locaux.values() if v == "KO")
        local_warn = sum(1 for v, _ in r.tests_locaux.values() if v == "WARN")
        local_str = "[green]OK[/]" if local_ko == 0 else f"[red]{local_ko}KO[/]"
        if local_warn:
            local_str += f"/[yellow]{local_warn}W[/]"

        col_risque = COULEUR_RISQUE.get(r.niveau_risque, "white")
        micro_str = "—" if not r.micro_transaction[0] else r.micro_transaction[0]

        table.add_row(
            r.carte.numero_formate(),
            r.partie_carte,
            r.carte.date_fr(),
            r.carte.cvv,
            local_str,
            r.api_stripe[0] or "—",
            r.banque_fr[0] or "—",
            r.stripe_radar[0] or "—",
            micro_str,
            f"[{col_risque}]{r.niveau_risque}[/]",
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

    # Compteur d'anomalies
    anomalies = sum(1 for r in resultats if r.statut_final != "OK")
    if total > 0:
        taux = anomalies / total
        if taux >= SEUIL_WARN_ANOMALIES:
            warn(
                f"Taux d'anomalies élevé : {taux*100:.0f}% "
                f"({anomalies}/{total}). Vérifie ton fichier de test."
            )
        else:
            info(f"Taux d'anomalies : {taux*100:.0f}% ({anomalies}/{total}).")

    # Listes détaillées
    if par_statut.get("OK"):
        info("\nCartes fonctionnelles :")
        for c in par_statut["OK"]:
            console.print(
                f"  [green]+[/] {c.carte.numero_formate()} "
                f"({c.partie_carte}, {c.carte.titulaire or 'sans titulaire'}) — "
                f"risque {c.niveau_risque}"
            )
    for s in (
        "BLOQUÉE", "EXPIRÉE", "INEXISTANTE", "CARTE_INEXISTANTE",
        "FRAUDEUSE", "PLAFOND_ATTEINT", "INVALIDE",
    ):
        lst = par_statut.get(s, [])
        if lst:
            warn(f"\nCartes {s} :")
            for c in lst:
                console.print(
                    f"  [{COULEUR_STATUT.get(s, 'white')}]•[/] "
                    f"{c.carte.numero_formate()} ({c.partie_carte})  →  {c.raison}"
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


def exporter_json(resultats: list[Resultat], chemin: Path, seed: int | None) -> None:
    data = {
        "version": VERSION,
        "date": datetime.now().isoformat(timespec="seconds"),
        "seed_globale": seed,
        "total": len(resultats),
        "resultats": [
            {
                "carte": asdict(r.carte),
                "partie_carte": r.partie_carte,
                "statut_final": r.statut_final,
                "raison": r.raison,
                "montant_max": r.montant_max,
                "seed": r.seed,
                "niveau_risque": r.niveau_risque,
                "tests_locaux": r.tests_locaux,
                "simulations": {
                    "api_stripe": r.api_stripe,
                    "banque_fr": r.banque_fr,
                    "prepayee": r.prepayee,
                    "fraude": r.fraude,
                    "amazon_like": r.amazon_like,
                    "stripe_radar": r.stripe_radar,
                    "banque_fr_v2": r.banque_fr_v2,
                    "micro_transaction": r.micro_transaction,
                },
            }
            for r in resultats
        ],
    }
    chemin.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
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


def traiter(cartes: list[Carte], args: argparse.Namespace) -> list[Resultat]:
    if not cartes:
        warn("Aucune carte à tester.")
        return []
    n = len(cartes)
    info(f"Lancement des tests sur {n} carte(s) (micro-tx={args.micro_transaction})...\n")
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
                resultats.append(valider_carte(c, verbose=args.verbose, micro_tx=args.micro_transaction))
                progress.update(task, advance=1)
                if n >= SEUIL_COMPTEUR_TEMPS_REEL:
                    i = len(resultats)
                    if i % max(1, n // 10) == 0:
                        pct = int(i * 100 / n)
                        info(f"  → {i}/{n} cartes traitées ({pct}%)")
    else:
        for i, c in enumerate(cartes, 1):
            resultats.append(valider_carte(c, verbose=args.verbose, micro_tx=args.micro_transaction))
            if n >= SEUIL_COMPTEUR_TEMPS_REEL and i % max(1, n // 10) == 0:
                pct = int(i * 100 / n)
                info(f"  → {i}/{n} cartes traitées ({pct}%)")

    if args.verbose:
        afficher_table_detaillee(resultats)
    afficher_resume(resultats)
    return resultats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testuer_carte_bleue_v2",
        description=(
            "Testeur LOCAL de cartes bleues — 100% offline, simulation "
            "de 7 processeurs (Stripe, banque FR, prépayée, fraude, "
            "Amazon-like, Radar, banque FR v2)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python testuer_carte_bleue_v2.py
  python testuer_carte_bleue_v2.py --fichier test_v2.txt
  python testuer_carte_bleue_v2.py --fichier test_v2.txt --seed 42 --verbose
  python testuer_carte_bleue_v2.py --fichier test_v2.txt --micro-transaction
  python testuer_carte_bleue_v2.py --fichier test_v2.txt --export-csv out.csv
  python testuer_carte_bleue_v2.py --manuel --limite 50

⚠️  Usage éducatif uniquement — 100% offline, aucun appel réseau.
""",
    )
    parser.add_argument("--manuel", "-m", action="store_true", help="Mode manuel interactif.")
    parser.add_argument("--fichier", "-f", type=Path, help="Chemin du fichier TXT/CSV à charger.")
    parser.add_argument("--limite", "-l", type=int, default=LIMITE_PAR_DEFAUT,
                        help=f"Nombre max de cartes (défaut {LIMITE_PAR_DEFAUT}).")
    parser.add_argument("--seed", type=int, default=None, help="Seed globale pour reproductibilité.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Table détaillée.")
    parser.add_argument("--micro-transaction", action="store_true",
                        help="Active le test de micro-transaction (0.01€/0.50€/0.99€/1.99€).")
    parser.add_argument("--export-csv", type=Path, default=None, help="Export CSV des résultats.")
    parser.add_argument("--export-json", type=Path, default=None, help="Export JSON des résultats.")
    parser.add_argument("--no-clear", action="store_true", help="Ne pas effacer l'écran.")
    parser.add_argument("--no-rich-warn", action="store_true", help="Masque le warning rich.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION} ({DATE_BUILD})")
    args = parser.parse_args(argv)

    if args.seed is not None:
        random.seed(args.seed)
    if not args.no_clear:
        clear_screen()

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
