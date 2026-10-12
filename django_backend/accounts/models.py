from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone

from core.utils import new_uuid


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Un compte doit avoir une adresse e-mail.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", "admin")
        extra.setdefault("is_active", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser):
    id = models.CharField(primary_key=True, max_length=36, default=new_uuid, editable=False)
    email = models.EmailField(max_length=255, unique=True)
    # La colonne s'appelle "hashed_password" en base (heritage de la version
    # precedente) : db_column evite d'avoir a renommer la colonne, donc evite
    # de toucher aux comptes existants.
    password = models.CharField(max_length=255, db_column="hashed_password")
    full_name = models.CharField(max_length=255, null=True, blank=True)
    role = models.CharField(max_length=50, default="admin")  # "admin" ou "operator"
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    # Conserve de la version precedente. Avec les sessions Django (stockees
    # cote serveur), l'invalidation a la deconnexion est native : ce compteur
    # n'est plus necessaire au fonctionnement, mais la colonne est gardee pour
    # ne pas modifier le schema d'une base en production.
    token_version = models.IntegerField(default=0)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"

    def __str__(self):
        return self.email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    # Django verifie ces attributs sur les comptes du site d'administration.
    # L'application n'utilise pas l'admin Django (les ecrans de gestion sont
    # dans le frontend), mais les definir evite toute surprise.
    @property
    def is_staff(self) -> bool:
        return self.is_admin

    @property
    def is_superuser(self) -> bool:
        return self.is_admin

    def has_perm(self, perm, obj=None):
        return self.is_admin

    def has_module_perms(self, app_label):
        return self.is_admin
