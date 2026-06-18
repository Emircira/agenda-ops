"""Hesap kara listesi — LLM hesap analizinde otomatik elenecek dev/resmi hesaplar.

`subject_top_account` secimi yaparken, istatistiksel 'dev hesap' elemesine (medyan
x kat) EK OLARAK buradaki hesaplar her zaman elenir. Cumhurbaskani, bakanliklar,
resmi kurum/parti hesaplari ve parti genel baskanlari gibi 'orta seviye' sayilamayacak
hesaplar buraya elle eklenir.

Yeni isim eklemek icin `ACCOUNT_BLOCKLIST_RAW` listesine ham adi/handle'i eklemek
yeterli; karsilastirma normalize edilerek (kucuk harf, '@' ve bosluk temizligi,
Turkce karakterler) yapilir.
"""

from __future__ import annotations

# Ham (insan tarafindan okunan) adlar / handle'lar. '@' veya bosluk fark etmez.
ACCOUNT_BLOCKLIST_RAW = [
    # --- Cumhurbaskani ---
    "Recep Tayyip Erdogan",
    "Recep Tayyip Erdoğan",
    "RTErdogan",
    "tcbestepe",
    "Cumhurbaskanligi",
    "Cumhurbaşkanlığı",
    "T.C. Cumhurbaşkanlığı",
    "Cumhurbaşkanlığı İletişim Başkanlığı",
    # --- Parti genel baskanlari (orta seviye sayilmaz) ---
    "Ümit Özdağ", "umitozdag",
    "Özgür Özel", "eczozgurozel",
    "Devlet Bahçeli", "dbdevletbahceli",
    "Ekrem İmamoğlu", "ekrem_imamoglu",
    "Mansur Yavaş", "mansuryavas06",
    "Meral Akşener", "meral_aksener",
    "Müsavat Dervişoğlu", "mdervisoglu",
    "Fatih Erbakan", "fatiherbakann",
    "Temel Karamollaoğlu", "temelkaramolla",
    "Ahmet Davutoğlu", "Ahmet_Davutoglu",
    "Ali Babacan", "babacanali",
    "Tuncer Bakırhan", "tuncbakirhan",
    "Tülay Hatimoğulları",
    # --- Resmi parti hesaplari ---
    "zaferpartisi",
    "herkesicinCHP",
    "akparti",
    "MHP_Bilgi",
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
        t.replace("İ", "i")
        .replace("I", "ı")
        .replace("Ş", "ş")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
        .lower()
    )
    return " ".join(t.split())


ACCOUNT_BLOCKLIST = frozenset(normalize_account(n) for n in ACCOUNT_BLOCKLIST_RAW)


def is_blocklisted(name: str) -> bool:
    """Verilen hesap adi kara listede mi?"""
    return normalize_account(name) in ACCOUNT_BLOCKLIST
