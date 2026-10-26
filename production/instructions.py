"""Makineye özel iş talimatı.

Tam reçete makine belgesine konmaz: her operasyon türü yalnız kendi işine gereken teknik bilgiyi alır
(extruder karışımı, matbaa baskı talimatı vb.). Üretim personeli talimatı yalnız açık (serbest/başlamış) iş
emrinde ve kendi kapsamındaki operasyonda görür; kapanmış/iptal işte erişim kapanır.
"""
import hashlib
import json

from django.db import transaction
from django.utils import timezone

from accounts.permissions import can, check
from catalog.models import DocumentVisibility, MachineType
from core.exceptions import BusinessRuleError
from core.services import audit

from .models import (AckKind, InstructionAck, InstructionPrint, InstructionStatus, WorkInstruction,
                     WorkOrderOperation, WorkOrderStatus)

OPEN_FOR_FLOOR = (WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED)


def _q(value):
    return None if value is None else format(value.normalize(), "f")


def technical_info(op):
    """Operasyon türüne göre gereken teknik alt küme (sabitlenmiş revizyondan)."""
    rev = op.work_order.recipe_revision
    info = {"revizyon": f"Rev.{rev.rev_no}"}
    if op.machine_type == MachineType.EXTRUDER:
        info.update({"en_mm": _q(rev.width_mm), "kalinlik_mikron": _q(rev.thickness_micron), "renk": rev.color,
                     "karisim": [{"malzeme": c.material.code, "ad": c.material.name, "oran": _q(c.percentage)}
                                 for c in rev.components.select_related("material")]})
    elif op.machine_type == MachineType.PRINT:
        info.update({"renk": rev.color, "baski": rev.print_spec or "—", "en_mm": _q(rev.width_mm)})
    elif op.machine_type == MachineType.PERFORATION:
        info.update({"perfore": rev.perforation_spec or "—", "en_mm": _q(rev.width_mm)})
    elif op.machine_type == MachineType.SLITTING:
        info.update({"en_mm": _q(rev.width_mm), "rulo_boyu_m": _q(rev.length_m)})
    info["proses"] = rev.process_notes or ""
    return info


TECH_LABELS = {"revizyon": "Teknik revizyon", "en_mm": "En (mm)", "kalinlik_mikron": "Kalınlık (µ)", "renk": "Renk",
               "baski": "Baskı talimatı", "perfore": "Perfore talimatı", "rulo_boyu_m": "Rulo boyu (m)",
               "proses": "Proses notu"}


def can_view_technical(user, op):
    if can(user, "recipe.view"):
        return True
    return op.work_order.status in OPEN_FOR_FLOOR and can(user, "instruction.view", op)


def build_snapshot(op):
    wo = op.work_order
    last = op.seq == wo.operations.order_by("-seq").values_list("seq", flat=True).first()
    customer = wo.product.customer
    docs = [{"baslik": d.title, "surum": d.version, "tur": d.get_kind_display(),
             "tarih": d.valid_from.isoformat() if d.valid_from else None}
            for d in customer.documents.filter(is_active=True, visibility=DocumentVisibility.OPERATIONS)]
    return {
        "is_emri": wo.code, "operasyon": op.code, "sira": op.seq, "operasyon_turu": op.get_machine_type_display(),
        "urun": f"{wo.product.code} – {wo.product.name}", "makine": f"{op.machine.code} {op.machine.name}",
        "planlanan_tarih": op.planned_date.isoformat() if op.planned_date else None,
        "vardiya": str(op.shift) if op.shift else None, "calisma_sirasi": op.queue_position,
        "hedef": f"{_q(wo.target_quantity)} {wo.get_unit_display()}",
        "teknik": technical_info(op),
        "paketleme": (customer.packing_instructions if last else ""),
        "etiketleme": (customer.labeling_instructions if last else ""),
        "belgeler": docs if last else [],
    }


def _hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def issue_for_print(actor, op):
    """Yazdırma talebi: içerik değişmediyse geçerli sürüm yeniden kullanılır; değiştiyse yeni sürüm açılır ve önceki
    'güncellendi' olarak işaretlenir. Her yazdırma ayrı kayıt edilir."""
    op = WorkOrderOperation.objects.select_for_update(of=("self",)).select_related("work_order__product__customer", "machine",
                                                                       "shift").get(pk=op.pk)
    check(actor, "instruction.print", op)
    if op.work_order.status not in OPEN_FOR_FLOOR:
        raise BusinessRuleError("Talimat yalnız serbest bırakılmış veya üretimdeki iş emri için basılabilir.")
    snapshot = build_snapshot(op)
    digest = _hash(snapshot)
    current = op.instructions.filter(status=InstructionStatus.CURRENT).first()
    if current is None or current.content_hash != digest:
        if current is not None:
            current.status, current.status_reason = InstructionStatus.SUPERSEDED, "İçerik değişti; yeni sürüm basıldı"
            current.status_changed_at = timezone.now()
            current.save()
        version = (op.instructions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        current = WorkInstruction.objects.create(operation=op, version=version, content_hash=digest, snapshot=snapshot,
                                                 created_by=actor)
    InstructionPrint.objects.create(instruction=current, user=actor)
    audit(actor, "instruction.print", op, {"talimat": (None, current.code)})
    return current


@transaction.atomic
def acknowledge(actor, instruction, kind):
    ins = WorkInstruction.objects.select_related("operation__work_order").get(pk=instruction.pk)
    check(actor, "instruction.view", ins.operation)
    if kind not in AckKind.values:
        raise BusinessRuleError("Geçersiz teslim türü.")
    if ins.status != InstructionStatus.CURRENT:
        raise BusinessRuleError("Bu talimat sürümü geçersiz; güncel sürümü açın.")
    if ins.operation.work_order.status not in OPEN_FOR_FLOOR:
        raise BusinessRuleError("İş emri açık değil.")
    ack = InstructionAck.objects.create(instruction=ins, user=actor, kind=kind)
    audit(actor, "instruction.ack", ins.operation, {"talimat": (None, f"{ins.code} {ack.get_kind_display()}")})
    return ack
