"""
testeur_carte_bleue.py
======================

Outil de test / debug / apprentissage pour valider la logique de
traitement des numéros de carte bleue dans TON code.

IMPORTANT :
-----------
Ce script n'effectue AUCUNE requête réseau. Il ne contacte aucune
banque, aucun processeur de paiement, aucune API en ligne. Il sert
uniquement à tester locallyement des fonctions de validation
(Luhn, dates, CVV, montants, etc.) que tu aurais écrites dans ton
propre code.

Il simule des cartes à des fins éducatives. Aucune transaction réelle
n'est possible. N'utilise pas ce script pour traiter de vraies
données bancaires en production.

Auteur : généré par Kilo (assistant CLI)
Python : 3.10+
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Affichage coloré via `rich` si dispo, sinon fallback en texte brut.
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    console = Console()
    USE_RICH = True
except Exception:  # pragma: no cover
    USE_RICH = False

    class _Stub:
        def print(self, *a, **kw):
            print(*a)

    console = _Stub()


def _cprint(text: str, style: str | None = None) -> None:
    """Affiche du texte avec une couleur si rich est dispo."""
    if USE_RICH and style:
        console.print(text, style=style)
    else:
        console.print(text)


def _ok(msg: str) -> None:
    _cprint(f"[OK] {msg}", "green")


def _warn(msg: str) -> None:
    _cprint(f"[WARN] {msg}", "yellow")


def _err(msg: str) -> None:
    _cprint(f"[ERR] {msg}", "bold red")


def _info(msg: str) -> None:
    _cprint(f"[INFO] {msg}", "cyan")


# ---------------------------------------------------------------------------
# Modèle de données
# ---------------------------------------------------------------------------
@dataclass
class Carte:
    numero: str
    mois: int
    annee: int
    cvv: str
    titulaire: str = ""

    def numero_formate(self) -> str:
        """Retourne le numéro groupé par 4 pour affichage."""
        n = re.sub(r"\s+", "", self.numero)
        return " ".join(n[i : i + 4] for i in range(0, len(n), 4))


@dataclass
class Resultat:
    carte: Carte
    tests: dict[str, tuple[str, str]] = field(default_factory=dict)
    statut_final: str = "INCONNU"
    raison: str = ""
    montant_max: int = 0

    def ajouter_test(self, nom: str, statut: str, detail: str = "") -> None:
        self.tests[nom] = (statut, detail)


# ---------------------------------------------------------------------------
# Fonctions de validation (toutes locales, aucune requête réseau)
# ---------------------------------------------------------------------------
MONTANT_DU_JOUR_PAR_DEFAUT = 100  # plafond simulé en euros


def validation_format(c: Carte) -> tuple[bool, str]:
    """Test 1 : format du numéro (longueur 13-19, que des chiffres)."""
    n = re.sub(r"\s+", "", c.numero)
    if not n.isdigit():
        return False, "Le numéro contient des caractères non numériques."
    if not (13 <= len(n) <= 19):
        return False, f"Longueur invalide ({len(n)} chiffres, attendu 13-19)."
    if n.startswith("0"):
        return False, "Un numéro de carte ne commence jamais par 0."
    return True, f"Format OK ({len(n)} chiffres)."


def validation_luhn(c: Carte) -> tuple[bool, str]:
    """Test 2 : algorithme de Luhn."""
    n = re.sub(r"\s+", "", c.numero)
    if not n.isdigit():
        return False, "Impossible de calculer Luhn sur des non-chiffres."
    total = 0
    inverse = n[::-1]
    for i, ch in enumerate(inverse):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    if total % 10 == 0:
        return True, "Somme de Luhn congrue à 0 modulo 10."
    return False, f"Somme de Luhn = {total} (non divisible par 10)."


def validation_date(c: Carte, aujourd_hui: date | None = None) -> tuple[bool, str]:
    """Test 3 : la date d'expiration est-elle valide et non dépassée ?"""
    aujourd_hui = aujourd_hui or date.today()
    if not (1 <= c.mois <= 12):
        return False, f"Mois invalide ({c.mois})."
    if c.annee < 100:
        annee = 2000 + c.annee
    else:
        annee = c.annee
    # La carte expire le dernier jour du mois
    if annee < aujourd_hui.year:
        return False, f"Carte expirée (annee {annee} < {aujourd_hui.year})."
    if annee == aujourd_hui.year and c.mois < aujourd_hui.month:
        return False, (
            f"Carte expirée (mois {c.mois:02d}/{annee} "
            f"< {aujourd_hui.month:02d}/{aujourd_hui.year})."
        )
    return True, f"Valide jusqu'à {c.mois:02d}/{annee}."


def validation_cvv(c: Carte) -> tuple[bool, str]:
    """Test 4 : CVV = 3 ou 4 chiffres, et non-trivial (pas 000 ou 123)."""
    if not c.cvv.isdigit():
        return False, "Le CVV doit être uniquement composé de chiffres."
    if not (3 <= len(c.cvv) <= 4):
        return False, f"Longueur CVV invalide ({len(c.cvv)})."
    if c.cvv in {"000", "123", "9999"}:
        return False, "CVV trivial détecté (000/123/9999)."
    return True, f"CVV OK ({len(c.cvv)} chiffres)."


def calcul_montant_max(c: Carte) -> int:
    """
    Test 5 : simulation d'un plafond journalier.
    On génère un montant pseudo-aléatoire mais DÉTERMINISTE à partir
    du numéro de carte, pour des résultats reproductibles.
    """
    seed = sum(int(ch) for ch in c.numero if ch.isdigit())
    rng = random.Random(seed)
    # Plafond entre 50 € et 5000 €
    return rng.choice([50, 100, 150, 300, 500, 1000, 2000, 5000])


def tests_complementaires(c: Carte) -> list[tuple[str, bool, str]]:
    """Tests additionnels inventés pour le fun (et le réalisme)."""
    n = re.sub(r"\s+", "", c.numero)
    resultats: list[tuple[str, bool, str]] = []

    # Somme pairs/impairs doit être ~équilibrée
    pairs = sum(int(ch) for ch in n[::2])
    impairs = sum(int(ch) for ch in n[1::2])
    diff = abs(pairs - impairs)
    equilibre = diff <= 30
    resultats.append(
        (
            "Équilibre pairs/impairs",
            equilibre,
            f"pairs={pairs}, impairs={impairs}, |diff|={diff}",
        )
    )

    # Pas plus de 4 chiffres identiques d'affilée
    rep = re.search(r"(\d)\1{3,}", n)
    pas_de_repetition = rep is None
    resultats.append(
        (
            "Pas de répétition suspecte",
            pas_de_repetition,
            ("OK" if pas_de_repetition else f"Répétition de '{rep.group(1)}' détectée"),
        )
    )

    # Le titulaire ne doit pas être vide (si fourni)
    titulaire_ok = bool(c.titulaire.strip()) if c.titulaire else True
    resultats.append(
        (
            "Titulaire présent",
            titulaire_ok,
            c.titulaire or "(non renseigné)",
        )
    )

    return resultats


# ---------------------------------------------------------------------------
# Orchestration des tests sur une carte
# ---------------------------------------------------------------------------
def tester_carte(c: Carte) -> Resultat:
    """Exécute tous les tests sur une carte et retourne un Resultat."""
    r = Resultat(carte=c)

    # Test 1 : format
    ok, detail = validation_format(c)
    r.ajouter_test("Format", "OK" if ok else "EXISTE_PAS", detail)
    if not ok:
        r.statut_final = "EXISTE_PAS"
        r.raison = detail
        return r

    # Test 2 : Luhn
    ok, detail = validation_luhn(c)
    r.ajouter_test("Luhn", "OK" if ok else "NUMERO_INVALIDE", detail)
    if not ok:
        r.statut_final = "NUMERO_INVALIDE"
        r.raison = detail
        return r

    # Test 3 : date
    ok, detail = validation_date(c)
    r.ajouter_test("Date d'expiration", "OK" if ok else "EXPIRÉE", detail)
    if not ok:
        r.statut_final = "EXPIRÉE"
        r.raison = detail
        r.montant_max = calcul_montant_max(c)
        return r

    # Test 4 : CVV
    ok, detail = validation_cvv(c)
    r.ajouter_test("CVV", "OK" if ok else "BLOQUÉE", detail)
    if not ok:
        r.statut_final = "BLOQUÉE"
        r.raison = detail
        r.montant_max = calcul_montant_max(c)
        return r

    # Test 5 : montant max simulé
    montant = calcul_montant_max(c)
    r.montant_max = montant
    r.ajouter_test(
        "Montant max simulé",
        "OK",
        f"Plafond journalier simulé = {montant} €",
    )

    # Tests complémentaires
    for nom, ok, detail in tests_complementaires(c):
        r.ajouter_test(nom, "OK" if ok else "BLOQUÉE", detail)
        if not ok:
            r.statut_final = "BLOQUÉE"
            r.raison = f"{nom} : {detail}"
            return r

    # Si on arrive ici, tout est OK
    r.statut_final = "OK"
    r.raison = "Tous les tests locaux sont passés."
    return r


# ---------------------------------------------------------------------------
# Modes d'entrée
# ---------------------------------------------------------------------------
SEPARATEURS = ["|", ";", "\t"]


def _split_ligne(ligne: str) -> list[str] | None:
    """Découpe une ligne en [numero, mois, annee, cvv, nom]."""
    ligne = ligne.strip()
    if not ligne or ligne.startswith("#"):
        return None
    # CSV avec virgules
    if "," in ligne:
        parts = [p.strip() for p in ligne.split(",")]
    else:
        for sep in SEPARATEURS:
            if sep in ligne:
                parts = [p.strip() for p in ligne.split(sep)]
                break
        else:
            # Numéro seul sur la ligne, on génère le reste
            parts = [ligne]
    # Normalisation
    while len(parts) < 5:
        if len(parts) == 1:
            # Génère des valeurs plausibles à partir du numéro
            n = re.sub(r"\s+", "", parts[0])
            seed = sum(int(ch) for ch in n if ch.isdigit())
            rng = random.Random(seed)
            parts.extend(
                [
                    str(rng.randint(1, 12)),  # mois
                    str(rng.randint(26, 32)),  # annee 2026-2032
                    f"{rng.randint(100, 999)}",  # CVV
                    f"TESTEUR_{rng.randint(1, 99):02d}",  # nom
                ]
            )
        elif len(parts) == 2:
            parts.extend(["", "", ""])
        elif len(parts) == 3:
            parts.extend(["", ""])
        elif len(parts) == 4:
            parts.append("")
    return parts[:5]


def _parse_carte(parts: list[str]) -> Carte:
    """Construit une Carte à partir des champs bruts."""
    num, mois, annee, cvv, nom = parts
    num = re.sub(r"\s+", "", num)
    try:
        m = int(mois)
        a = int(annee)
    except ValueError as e:
        raise ValueError(f"Mois/Année non numériques: {mois!r}/{annee!r}") from e
    return Carte(numero=num, mois=m, annee=a, cvv=cvv, titulaire=nom)


def charger_fichier(chemin: Path, limite: int = 200) -> list[Carte]:
    """Charge un fichier TXT ou CSV et retourne la liste des cartes."""
    if not chemin.exists():
        raise FileNotFoundError(f"Fichier introuvable: {chemin}")
    contenu = chemin.read_text(encoding="utf-8", errors="ignore")
    lignes = contenu.splitlines()
    cartes: list[Carte] = []
    erreurs: list[str] = []

    for i, ligne in enumerate(lignes, 1):
        parts = _split_ligne(ligne)
        if parts is None:
            continue
        try:
            cartes.append(_parse_carte(parts))
        except ValueError as e:
            erreurs.append(f"Ligne {i}: {e}")
        if len(cartes) >= limite:
            _warn(f"Limite de {limite} cartes atteinte, le reste est ignoré.")
            break

    if erreurs:
        _warn(f"{len(erreurs)} ligne(s) ignorée(s) à cause d'erreurs de parsing.")
        for e in erreurs[:5]:
            _warn(f"  - {e}")
        if len(erreurs) > 5:
            _warn(f"  ... et {len(erreurs) - 5} autres.")

    if not cartes:
        raise ValueError("Aucune carte valide n'a été trouvée dans le fichier.")
    return cartes


def mode_manuel() -> list[Carte]:
    """Demande interactivement des cartes à l'utilisateur."""
    _info("Mode Manuel : entre tes cartes une par une.")
    _info("Format attendu : numero|mois|annee|CVV|nom  (champs optionnels)")
    _info("Laisse vide pour terminer.\n")

    cartes: list[Carte] = []
    while True:
        try:
            ligne = input("Carte (ou Entrée pour finir) > ").strip()
        except (EOFError, KeyboardInterrupt):
            _info("\nFin de saisie.")
            break
        if not ligne:
            break
        parts = _split_ligne(ligne)
        if parts is None:
            continue
        try:
            cartes.append(_parse_carte(parts))
            _ok(f"Carte #{len(cartes)} enregistrée.")
        except ValueError as e:
            _err(f"Erreur de parsing: {e}")
    return cartes


def mode_fichier_interactif() -> list[Carte]:
    """Demande le chemin d'un fichier et le charge."""
    while True:
        chemin_str = input("Chemin du fichier TXT/CSV > ").strip()
        if not chemin_str:
            return []
        chemin = Path(chemin_str).expanduser()
        try:
            return charger_fichier(chemin)
        except (FileNotFoundError, ValueError) as e:
            _err(str(e))
            _info("Réessaie avec un autre chemin.")


# ---------------------------------------------------------------------------
# Affichage des résultats
# ---------------------------------------------------------------------------
COULEUR_STATUT = {
    "OK": "green",
    "EXISTE_PAS": "red",
    "NUMERO_INVALIDE": "red",
    "EXPIRÉE": "yellow",
    "BLOQUÉE": "magenta",
}


def afficher_rapport(r: Resultat) -> None:
    """Affiche un rapport détaillé pour une carte."""
    c = r.carte
    statut_style = COULEUR_STATUT.get(r.statut_final, "white")
    header = f"Carte n° {c.numero_formate()}"
    if USE_RICH:
        console.print(Panel(header, style="bold cyan", box=box.ROUNDED))
    else:
        console.print("=" * 60)
        console.print(header)
        console.print("=" * 60)

    for nom, (st, detail) in r.tests.items():
        couleur = COULEUR_STATUT.get(st, "white")
        _cprint(f"  - {nom:25s} : [{couleur}]{st}[/{couleur}]  ({detail})")

    _cprint(f"  - {'Montant max autorisé':25s} : {r.montant_max} €", "cyan")
    _cprint(
        f"  - {'Statut final':25s} : [{statut_style}]{r.statut_final}[/{statut_style}]",
        None,
    )
    _cprint(f"  - {'Raison détaillée':25s} : {r.raison}\n")


def afficher_resume(resultats: list[Resultat]) -> None:
    """Affiche un résumé global de tous les tests."""
    total = len(resultats)
    par_statut: dict[str, list[Resultat]] = {}
    for r in resultats:
        par_statut.setdefault(r.statut_final, []).append(r)

    if USE_RICH:
        table = Table(title="Résumé global", box=box.SIMPLE_HEAVY)
        table.add_column("Statut", style="bold")
        table.add_column("Nombre", justify="right")
        table.add_column("Exemples", overflow="fold")
        for statut in ["OK", "BLOQUÉE", "EXPIRÉE", "NUMERO_INVALIDE", "EXISTE_PAS"]:
            cartes = par_statut.get(statut, [])
            exemples = ", ".join(c.carte.numero_formate() for c in cartes[:3])
            if len(cartes) > 3:
                exemples += f" ... (+{len(cartes) - 3})"
            table.add_row(
                f"[{COULEUR_STATUT.get(statut, 'white')}]{statut}[/]",
                str(len(cartes)),
                exemples or "—",
            )
        table.add_row("[bold]TOTAL[/]", str(total), "")
        console.print(table)
    else:
        console.print("\n=== RÉSUMÉ GLOBAL ===")
        for statut, cartes in par_statut.items():
            console.print(f"  {statut:20s} : {len(cartes)}")
        console.print(f"  {'TOTAL':20s} : {total}")

    # Listes détaillées
    if par_statut.get("OK"):
        _info("\nCartes fonctionnelles :")
        for c in par_statut["OK"]:
            console.print(f"  - {c.carte.numero_formate()} (titulaire: {c.carte.titulaire or '?'})")
    if par_statut.get("BLOQUÉE"):
        _warn("\nCartes bloquées :")
        for c in par_statut["BLOQUÉE"]:
            console.print(
                f"  - {c.carte.numero_formate()}  raison: {c.raison}"
            )
    if par_statut.get("EXPIRÉE"):
        _warn("\nCartes expirées :")
        for c in par_statut["EXPIRÉE"]:
            console.print(f"  - {c.carte.numero_formate()}")


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------+
BANNER = r"""
  _______            _              ____             _       _
 |__   __|          | |            |  _ \           | |     | |
    | | ___  _ __ __| | ___ _ __   | |_) |_   _  ___| | __ _| |_ ___  _ __
    | |/ _ \| '__/ _` |/ _ \ '__|  |  _ <| | | |/ __| |/ _` | __/ _ \| '__|
    | | (_) | | | (_| |  __/ |     | |_) | |_| | (__| | (_| | || (_) | |
    |_|\___/|_|  \__,_|\___|_|     |____/ \__,_|\___|_|\__,_|\__\___/|_|

            Outil LOCAL de test / debug / apprentissage.
            AUCUNE transaction réelle, AUCUN appel réseau.
"""


def menu_principal() -> str:
    """Affiche le menu et retourne le choix."""
    if USE_RICH:
        console.print(BANNER, style="bold blue")
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
        _err("Choix invalide.")


def executer_tests(cartes: Iterable[Carte]) -> list[Resultat]:
    """Exécute les tests sur toutes les cartes et affiche les rapports."""
    resultats = [tester_carte(c) for c in cartes]
    for r in resultats:
        afficher_rapport(r)
    afficher_resume(resultats)
    return resultats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Testeur LOCAL de cartes bleues (aucun appel réseau).",
    )
    parser.add_argument(
        "--fichier", "-f", type=Path, help="Chemin d'un fichier TXT/CSV à charger."
    )
    parser.add_argument(
        "--limite", "-l", type=int, default=200, help="Nombre max de cartes (défaut 200)."
    )
    parser.add_argument(
        "--mode", "-m", choices=["manuel", "fichier", "menu"], default="menu"
    )
    args = parser.parse_args(argv)

    if args.mode == "fichier" or args.fichier:
        try:
            cartes = charger_fichier(args.fichier, limite=args.limite) if args.fichier else mode_fichier_interactif()
        except (FileNotFoundError, ValueError) as e:
            _err(str(e))
            return 2
    elif args.mode == "manuel":
        cartes = mode_manuel()
    else:
        choix = menu_principal()
        if choix == "1":
            cartes = mode_manuel()
        elif choix == "2":
            cartes = mode_fichier_interactif()
        else:
            _info("Au revoir.")
            return 0

    if not cartes:
        _warn("Aucune carte à tester. Fin du script.")
        return 0

    _info(f"Lancement des tests sur {len(cartes)} carte(s)...\n")
    executer_tests(cartes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
