"""Verifie mecaniquement les regles du skill clinique-ui sur une page HTML.

Ces controles ne disent pas si une page est BELLE — ca, seul un humain le
juge. Ils attrapent les ecarts objectifs qui, dans ce projet, sont les causes
les plus frequentes d'une interface qui fait datee ou incoherente.

Usage :
    python verifier_page.py <fichier.html> [autre.html ...]
    python verifier_page.py --json <fichier.html>     # sortie exploitable

Code de sortie : 0 si tout passe, 1 sinon.
"""
import json
import re
import sys
import unicodedata

# L'echelle du skill. Une septieme taille signale presque toujours un element
# au mauvais niveau de hierarchie plutot qu'un besoin reel.
ECHELLE_POLICE = {"1.75rem", "1.15rem", "0.95rem", "0.9375rem", "0.85rem", "0.75rem"}

# Tolerees : valeurs relatives et mots-cles, qui ne cassent pas l'echelle, et
# les valeurs calculees en JavaScript (`font-size: ${taille}px`) — le tableur
# ajuste la police au zoom, ce n'est pas une taille ecrite en dur.
POLICE_TOLEREE = re.compile(r"^(inherit|smaller|larger|\d+%|calc\(.*\)|var\(.*\))$|\$\{")

ICONES_ATTENDUES = re.compile(r"icon\(['\"]|data-icon=")
COULEUR_EN_DUR = re.compile(r"(?:color|background(?:-color)?|border(?:-color)?)\s*:\s*(#[0-9a-fA-F]{3,8}|rgb)")


def _emojis(texte: str):
    """Pictogrammes utilises comme icones. On ignore ce qui est dans un
    commentaire : le skill autorise d'en parler, pas de s'en servir."""
    sans_commentaires = re.sub(r"<!--.*?-->|/\*.*?\*/|//[^\n]*", "", texte, flags=re.S)
    trouves = []
    for c in sans_commentaires:
        if unicodedata.category(c) == "So" and ord(c) > 0x2100:
            trouves.append(c)
    return sorted(set(trouves))


def verifier(chemin: str) -> dict:
    texte = open(chemin, encoding="utf-8").read()
    ecarts = []

    if "initAppShell(" not in texte:
        ecarts.append("n'appelle pas initAppShell() : la coquille commune "
                      "(barre laterale, bouton retour, indicateur hors-ligne) manquera")

    emo = _emojis(texte)
    if emo:
        ecarts.append(f"utilise {len(emo)} emoji(s) la ou une icone du jeu local est attendue : "
                      + " ".join(emo[:10]))

    if not ICONES_ATTENDUES.search(texte):
        ecarts.append("n'utilise aucune icone du jeu local (icon(...) ou data-icon=)")

    couleurs = COULEUR_EN_DUR.findall(texte)
    if couleurs:
        ecarts.append(f"{len(couleurs)} couleur(s) ecrite(s) en dur au lieu des variables de style.css")

    tailles = set(re.findall(r"font-size:\s*([^;}\"']+)", texte))
    hors_echelle = {t.strip() for t in tailles
                    if t.strip() not in ECHELLE_POLICE and not POLICE_TOLEREE.match(t.strip())}
    if hors_echelle:
        ecarts.append(f"{len(hors_echelle)} taille(s) de police hors de l'echelle : "
                      + ", ".join(sorted(hors_echelle)[:8]))

    # Bordure ET ombre sur la meme regle : l'une des deux est de trop.
    double = [b for b in re.findall(r"\{[^{}]*\}", texte)
              if "border:" in b and "box-shadow:" in b and "none" not in b]
    if double:
        ecarts.append(f"{len(double)} regle(s) cumulent une bordure et une ombre")

    # On cherche la classe dediee OU une formulation d'etat vide. Se limiter au
    # mot "vide" produisait de faux signalements sur des pages qui disent
    # pourtant correctement "Aucun patient ne correspond a ces filtres" — et un
    # verificateur qui crie au loup finit ignore.
    if not re.search(r"empty-state|aucune?\s|rien\s|pas\s+encore", texte, re.I):
        ecarts.append("aucun etat vide reperable : un ecran sans donnees paraitra casse")

    return {"fichier": chemin, "conforme": not ecarts, "ecarts": ecarts}


def main():
    # La console Windows est en cp1252 par defaut et ne sait pas afficher les
    # emojis que ce script a justement pour role de signaler.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = [a for a in sys.argv[1:] if a != "--json"]
    en_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        return 1

    resultats = [verifier(c) for c in args]
    if en_json:
        print(json.dumps(resultats, ensure_ascii=False, indent=2))
    else:
        for r in resultats:
            etat = "CONFORME" if r["conforme"] else f"{len(r['ecarts'])} ecart(s)"
            print(f"\n{r['fichier']} — {etat}")
            for e in r["ecarts"]:
                print(f"  - {e}")
    return 0 if all(r["conforme"] for r in resultats) else 1


if __name__ == "__main__":
    sys.exit(main())
