from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class Role(models.TextChoices):
    ADMIN = "yonetici", "Yönetici"
    SALES = "satis", "Satış"
    PLANNING = "planlama", "Planlama"
    PRODUCTION = "uretim", "Üretim / Ustabaşı"
    WAREHOUSE = "depo", "Depo"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, personnel_no, password, full_name="", role=Role.SALES, **extra):
        if not personnel_no:
            raise ValueError("Personel numarası zorunludur.")
        if not password:
            raise ValueError("Kişisel parola zorunludur.")
        user = self.model(personnel_no=personnel_no, full_name=full_name, role=role, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, personnel_no, password, full_name="Sistem Yöneticisi", **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(personnel_no, password, full_name=full_name, role=Role.ADMIN, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    personnel_no = models.CharField("Personel no", max_length=20, unique=True)
    full_name = models.CharField("Ad soyad", max_length=120)
    role = models.CharField("Rol", max_length=20, choices=Role.choices)
    is_active = models.BooleanField("Aktif", default=True)
    is_staff = models.BooleanField("Yönetim paneli erişimi", default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "personnel_no"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Kullanıcı"
        verbose_name_plural = "Kullanıcılar"
        ordering = ["personnel_no"]

    def __str__(self):
        return f"{self.personnel_no} – {self.full_name}"

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.personnel_no
