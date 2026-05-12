"""
ysk_raw klasör adları → ElectionCategory + semantik election_detail (slug).
2015 çift seçim, 2023 CB turları, 2019 İstanbul yenilemesi vb.
"""

from __future__ import annotations

import re

from app.models.core import ElectionCategory


def detect_base_category(folder_lower: str) -> ElectionCategory:
    fl = folder_lower
    if "cb" in fl or "cumhurbaskan" in fl:
        return ElectionCategory.presidential
    if "milletvekili" in fl or ("genel" in fl and "cb" not in fl and "referandum" not in fl):
        return ElectionCategory.parliamentary
    if "referandum" in fl or "refarandum" in fl:
        return ElectionCategory.referendum
    return ElectionCategory.local


def folder_to_slug(type_folder: str) -> str:
    """Dosya sistemi güvenli, stabil etiket."""
    s = type_folder.strip().replace(" ", "_")
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:180] or "varsayilan") if s else "varsayilan"


def resolve_election_scope(year: int, type_folder: str) -> tuple[ElectionCategory, str]:
    """
    (kategori, election_detail slug).
    Özel tarihler önce ele alınır (normal il_yerel ile karışmaması için).
    """
    fl = type_folder.lower()

    # 2019 Büyükşehir yenileme (İstanbul) — normal 2019 il_yerel'den ayrı
    if year == 2019 and "yenilen" in fl:
        return ElectionCategory.local, "2019_il_yerel_yenilenme"

    # 2023 Cumhurbaşkanlığı iki tur
    if year == 2023 and "cb" in fl and ("14mayis" in fl or "14_mayis" in fl):
        return ElectionCategory.presidential, "2023_cb_1_tur"
    if year == 2023 and "cb" in fl and ("28mayis" in fl or "28_mayis" in fl):
        return ElectionCategory.presidential, "2023_cb_2_tur"

    # 2015 Haziran / Kasım milletvekili genel
    if year == 2015 and "yirmibes" in fl:
        return ElectionCategory.parliamentary, "2015_1"
    if year == 2015 and "yirmialti" in fl:
        return ElectionCategory.parliamentary, "2015_2"

    cat = detect_base_category(fl)
    return cat, folder_to_slug(type_folder)


def is_referendum_il_result_filename(file_lower: str) -> bool:
    fl = file_lower.replace(" ", "")
    if "ilreferandum" in fl:
        return True
    if "referandum" in fl and "il" in fl and "sonuc" in fl:
        return True
    return False
