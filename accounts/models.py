from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class Role(models.TextChoices):
    """Başlangıç rol şablonlarının kodları (accounts.catalog.TEMPLATES). Roller veritabanında yönetilir."""

    ADMIN = "yonetici", "Yönetici"
    HR = "ik", "İK"
    SALES = "satis", "Satış"
    PLANNING = "planlama", "Planlama"
    PRODUCTION_MANAGER = "uretim_yetkilisi", "Üretim yetkilisi"
    PRODUCTION = "ustabasi", "Ustabaşı"
    OPERATOR = "operator", "Usta / operatör"
    QUALITY = "kalite", "Kalite sorumlusu"
    WAREHOUSE_MANAGER = "ambar_yetkilisi", "Ambar yetkilisi"
    WAREHOUSE = "depo", "Depo personeli"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, personnel_no, password, full_name="", role=None, **extra):
        """role verilirse aynı kodlu rol atanır (kurulum/test kolaylığı; kayıt altına alınan atama
        accounts.services üzerinden yapılır)."""
        if not personnel_no:
            raise ValueError("Personel numarası zorunludur.")
        if not password:
            raise ValueError("Kişisel parola zorunludur.")
        user = self.model(personnel_no=personnel_no, full_name=full_name, **extra)
        user.set_password(password)
        user.save(using=self._db)
        if role:
            UserRole.objects.create(user=user, role=AccessRole.objects.get(code=role), reason="Kurulum")
        return user

    def create_superuser(self, personnel_no, password, full_name="Sistem Yöneticisi", **extra):
        """Django yönetim paneli için. Uygulama yetkisini is_superuser değil, yönetici rolü verir."""
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(personnel_no, password, full_name=full_name, role=Role.ADMIN, **extra)


class Shift(models.Model):
    code = models.CharField("Vardiya kodu", max_length=20, unique=True)
    name = models.CharField("Vardiya adı", max_length=60)
    start_time = models.TimeField("Başlangıç")
    end_time = models.TimeField("Bitiş")
    is_active = models.BooleanField("Aktif", default=True)

    class Meta:
        ordering = ["start_time", "code"]
        verbose_name = "Vardiya"
        verbose_name_plural = "Vardiyalar"

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"


class User(AbstractBaseUser, PermissionsMixin):
    personnel_no = models.CharField("Personel no", max_length=20, unique=True)
    full_name = models.CharField("Ad soyad", max_length=120)
    team = models.CharField("Ekip", max_length=60, blank=True)
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Vardiya")
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

    def active_roles(self):
        return AccessRole.objects.filter(status=RoleStatus.ACTIVE, assignments__user=self,
                                         assignments__revoked_at__isnull=True).distinct()

    @property
    def role_names(self):
        return ", ".join(r.name for r in self.active_roles()) or "Rol yok"


class RoleStatus(models.TextChoices):
    DRAFT = "taslak", "Taslak (onay bekliyor)"
    ACTIVE = "aktif", "Aktif"
    INACTIVE = "pasif", "Pasif"


class AccessRole(models.Model):
    """Yönetilebilir rol. Yetkiler RolePermission ile (işlem + kapsam) verilir."""

    code = models.CharField("Rol kodu", max_length=40, unique=True)
    name = models.CharField("Rol adı", max_length=80)
    description = models.TextField("Açıklama", blank=True)
    status = models.CharField("Durum", max_length=10, choices=RoleStatus.choices, default=RoleStatus.DRAFT)
    is_template = models.BooleanField("Başlangıç şablonu", default=False)
    hr_assignable = models.BooleanField("İK atayabilir", default=False)
    created_by = models.ForeignKey("User", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey("User", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Rol"
        verbose_name_plural = "Roller"

    def __str__(self):
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(AccessRole, on_delete=models.CASCADE, related_name="permissions")
    action = models.CharField("İşlem", max_length=60)
    scope = models.CharField("Kapsam", max_length=20, default="all")

    class Meta:
        ordering = ["action"]
        constraints = [models.UniqueConstraint(fields=["role", "action"], name="uniq_role_action")]


class UserRole(models.Model):
    """Rol ataması. Kaldırılan atama silinmez, revoked_at ile kapanır (geçmiş korunur)."""

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="role_assignments")
    role = models.ForeignKey(AccessRole, on_delete=models.PROTECT, related_name="assignments")
    granted_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    granted_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    revoke_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-granted_at"]
        constraints = [models.UniqueConstraint(fields=["user", "role"], condition=models.Q(revoked_at__isnull=True),
                                               name="uniq_active_user_role")]


class UserMachine(models.Model):
    """Kullanıcının çalıştığı makineler (operatör 'makine' kapsamı)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="machine_links")
    machine = models.ForeignKey("catalog.Machine", on_delete=models.PROTECT, related_name="user_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "machine"], name="uniq_user_machine")]
