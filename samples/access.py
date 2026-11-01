"""Numune oluşturma kapsamı — tek kaynak.

Yeni numune formu, müşteriye göre ürün sorgusu, palet/bobin/iş emri kısayolları, detay sayfalarındaki "numune başlat"
bağlantıları ve `services.create_sample` aynı fonksiyonları kullanır; böylece arayüzde sunulan seçenek ile sunucunun
kabul ettiği kayıt ayrışmaz.

`sample.manage` kapsamına göre:
- all: tüm aktif müşteri ve ürünler; bağlı kayıtlar (iş emri/bobin/palet/sipariş kalemi) kısıtsız.
- customer: erişim verilmiş müşteriler ve onların kayıtları.
- assigned: yalnız kullanıcıya *şu anda* atanmış iş emirleri (`accounts.scope` ile aynı kural). Müşteri ve ürün
  seçeneği bu iş emirlerinin ürününden türetilir; numune bu iş emirlerinden birine bağlanmadan kaydedilemez ve bağlı
  bobin/palet/sipariş kalemi o iş emrine ait olmalıdır.

Seçenek olarak görünmek kayda genel erişim vermez: `customer.view`, `order.view`, `stock.view` vb. kapsamları ve
`accounts.scope` tabloları değişmez. Stok ayırma (`sample.stock`) ve kalite kararı (`sample.decide`) ayrı yetkidir.
Her değerlendirme istek anında veritabanından yapılır; devirden önce açılmış form kayıtta güncel atamayla denetlenir.
"""
from django.db.models import Q

from accounts.catalog import Scope
from accounts.permissions import scoped, scopes_for
from accounts.scope import accessible_customer_ids, scope_q

ACTION = "sample.manage"


def _scopes(user):
    return scopes_for(user, ACTION)


def assigned_only(user):
    """Kullanıcı numuneyi yalnız atanmış iş emri bağlamında açabiliyor mu?"""
    scopes = _scopes(user)
    return Scope.ASSIGNED in scopes and not scopes & {Scope.ALL, Scope.CUSTOMER}


def _assigned_work_orders(user):
    from production.models import WorkOrder
    if Scope.ASSIGNED not in _scopes(user):
        return WorkOrder.objects.none()
    return WorkOrder.objects.filter(scope_q(WorkOrder, Scope.ASSIGNED, user))


def _free_customer_q(user):
    """İş emri bağlamı gerekmeden kullanılabilen müşteriler (None: hiçbiri)."""
    scopes = _scopes(user)
    if Scope.ALL in scopes:
        return Q()
    if Scope.CUSTOMER in scopes:
        return Q(pk__in=accessible_customer_ids(user))
    return None


def customers(user):
    from catalog.models import Customer
    qs = Customer.objects.active()
    parts = [q for q in (_free_customer_q(user),) if q is not None]
    if Scope.ASSIGNED in _scopes(user):
        parts.append(Q(pk__in=_assigned_work_orders(user).values("product__customer_id")))
    if not parts:
        return qs.none()
    q = parts[0]
    for part in parts[1:]:
        q |= part
    return qs.filter(q)


def customer_needs_work_order(user, customer):
    """Bu müşteri için numune yalnız atanmış iş emri bağlamında mı açılabilir?"""
    from catalog.models import Customer
    free = _free_customer_q(user)
    return free is None or not Customer.objects.filter(free, pk=customer.pk).exists()


def products(user, customer):
    from catalog.models import Product
    qs = Product.objects.active().filter(customer=customer)
    if customer is None or not customers(user).filter(pk=customer.pk).exists():
        return qs.none()
    if not customer_needs_work_order(user, customer):
        return qs
    return qs.filter(pk__in=_assigned_work_orders(user).values("product_id"))


def revisions(user, product):
    """Formdaki revizyon seçenekleri: atanmış iş emri bağlamında yalnız o iş emirlerinin revizyonları."""
    from catalog.models import RecipeRevision
    qs = RecipeRevision.objects.filter(recipe__product=product).order_by("-rev_no")
    if not customer_needs_work_order(user, product.customer):
        return qs
    return qs.filter(pk__in=_assigned_work_orders(user).filter(product=product).values("recipe_revision_id"))


# --- Bağlanabilir kayıtlar (kodla çözümleme ve kısayollar) -------------------------------------------------------
def work_orders(user):
    from production.models import WorkOrder
    return scoped(WorkOrder.objects.all(), user, ACTION)


def coils(user):
    from production.models import Coil
    return scoped(Coil.objects.all(), user, ACTION)


def pallets(user):
    from warehouse.models import Pallet
    return scoped(Pallet.objects.all(), user, ACTION)


def order_lines(user):
    from orders.models import OrderLine
    qs = scoped(OrderLine.objects.all(), user, ACTION)  # müşteri kapsamı; atanmış kapsam tanımsız → boş
    if Scope.ASSIGNED in _scopes(user):
        qs = qs | OrderLine.objects.filter(pk__in=_assigned_work_orders(user).values("order_line_id"))
    return qs


def check_create(user, *, customer, product, work_order, order_line, coil, pallet):
    """Servisin kayıt anındaki kapsam denetimi. Hata nedeni: None (geçerli) veya (alan, mesaj)."""
    if not _scopes(user):
        return None, "Numune kaydetme yetkiniz yok."
    if not customers(user).filter(pk=customer.pk).exists():
        return "customer", "Bu müşteri için numune kaydetme yetkiniz yok."
    for obj, qs, field in ((work_order, work_orders, "work_order"), (coil, coils, "coil"),
                           (pallet, pallets, "pallet"), (order_line, order_lines, "order_line")):
        if obj is not None and not qs(user).filter(pk=obj.pk).exists():
            return field, "Bu kayıt numune için kullanılamaz."
    if not customer_needs_work_order(user, customer):
        return None
    # Yalnız atanmış iş emri bağlamı: iş emri zorunlu, şu anda bu kullanıcıya atanmış ve diğer bağlantılar ona ait.
    if work_order is None or not _assigned_work_orders(user).filter(pk=work_order.pk).exists():
        return "work_order", "Bu numune yalnız size atanmış bir iş emrine bağlanarak kaydedilebilir."
    if product is not None and product.pk != work_order.product_id:
        return "product", "Ürün bu iş emrinin ürünü değil."
    if order_line is not None and order_line.pk != work_order.order_line_id:
        return "order_line", "Sipariş kalemi bu iş emrine ait değil."
    if coil is not None and coil.work_order_id != work_order.pk:
        return "coil", "Bobin bu iş emrine ait değil."
    if pallet is not None and pallet.work_order_id != work_order.pk:
        return "pallet", "Palet bu iş emrinden gelmiyor."
    return None


def source_initial(user, *, work_order=None, coil=None, pallet=None):
    """Kaynak kayıttan bilinen bağlantılar (yalnız kullanıcının erişebildikleri)."""
    wo = work_order or (coil.work_order if coil is not None else None) or (pallet.work_order if pallet else None)
    product = pallet.product if pallet is not None else wo.product
    initial = {"customer": product.customer_id, "product": product.pk}
    if wo is not None and work_orders(user).filter(pk=wo.pk).exists() and wo.product_id == product.pk:
        initial["work_order_code"] = wo.code
        if wo.order_line_id and order_lines(user).filter(pk=wo.order_line_id).exists():
            initial["order_line_code"] = wo.order_line.code
    if coil is not None:
        initial["coil_code"] = coil.code
    if pallet is not None:
        initial["pallet_code"] = pallet.code
    return initial
