"""Hesap kara listesi — LLM hesap analizinde otomatik elenecek dev/resmi hesaplar.

`subject_top_account` secimi yaparken, istatistiksel 'dev hesap' elemesine (medyan
x kat) EK OLARAK buradaki hesaplar her zaman elenir. Cumhurbaskani, bakanliklar,
resmi kurum/parti hesaplari gibi 'orta seviye' sayilamayacak hesaplar buraya
elle eklenir.

Yeni isim eklemek icin `ACCOUNT_BLOCKLIST_RAW` listesine ham adi/handle'i eklemek
yeterli; karsilastirma normalize edilerek (kucuk harf, '@' ve bosluk temizligi,
Turkce karakterler) yapilir.
"""

from __future__ import annotations

# Ham (insan tarafindan okunan) adlar / handle'lar. '@' veya bosluk fark etmez.
ACCOUNT_BLOCKLIST_RAW = [
    # --- Cumhurbaskani ---
    "Recep Tayyip Erdogan",
    "Recep Tayyip Erdo\u011fan",
    "RTErdogan",
    "tcbestepe",
    "Cumhurbaskanligi",
    "Cumhurba\u015fkanl\u0131\u011f\u0131",
    "T.C. Cumhurba\u015fkanl\u0131\u011f\u0131",
    "Cumhurba\u015fkanl\u0131\u011f\u0131 \u0130leti\u015fim Ba\u015fkanl\u0131\u011f\u0131",
]


def normalize_account(name: str) -> str:
    """Hesap adini karsilastirma icin sadelestirir.

    '@' ve bastaki/sondaki bosluklar atilir, Turkce buyuk harfler dogru kucultulur,
    ic bosluklar tek bosluga indirilir.
    """
    if not name:
        return ""
    t = str(name).strip().lstrip("@").strip()
    t = (
        t.replace("\u0130", "i")
        .replace("I", "\u0131")
        .replace("\u015e", "\u015f")
        .replace("\u011e", "\u011f")
        .replace("\u00dc", "\u00fc")
        .replace("\u00d6", "\u00f6")
        .replace("\u00c7", "\u00e7")
        .lower()
    )
    return " ".join(t.split())


ACCOUNT_BLOCKLIST = frozenset(normalize_account(n) for n in ACCOUNT_BLOCKLIST_RAW)


def is_blocklisted(name: str) -> bool:
    """Verilen hesap adi kara listede mi?"""
    return normalize_account(name) in ACCOUNT_BLOCKLIST
