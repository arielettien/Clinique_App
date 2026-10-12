"""Backend d'authentification."""
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Identique au backend standard de Django, a une exception pres : un
    compte desactive n'est PAS rejete ici.

    Pourquoi : le backend standard renvoie None pour un compte desactive,
    exactement comme pour un mot de passe faux — impossible alors de
    distinguer les deux cas. On laisse donc passer, et la vue de connexion
    affiche le bon message ("Compte desactive" plutot que "mot de passe
    incorrect"), ce qui evite a un agent de chercher pendant dix minutes une
    faute de frappe dans un mot de passe pourtant correct.

    La verification du compte desactive reste bien faite, juste un cran plus
    loin : aucun compte inactif ne peut ouvrir de session.
    """

    def user_can_authenticate(self, user):
        return True
