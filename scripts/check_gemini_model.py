#!/usr/bin/env python3
"""
Gemini model uyumluluk testi.

Kullanım:
  python3 scripts/check_gemini_model.py
  python3 scripts/check_gemini_model.py --prompt "Sadece OK yaz"

Çıkış kodları:
  0: Model seçildi ve generateContent smoke testi geçti.
  1: API anahtarı yok, model seçilemedi veya smoke test başarısız.
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import google.generativeai as genai

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.gemini_model import available_generate_content_models, choose_gemini_model


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini generateContent model test aracı")
    parser.add_argument("--prompt", default="Sadece OK yaz.", help="Smoke test prompt'u")
    parser.add_argument("--skip-generate", action="store_true", help="Sadece model seçimini test et")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY bulunamadı. .env dosyana anahtarı ekle.", file=sys.stderr)
        return 1

    genai.configure(api_key=api_key)
    available = available_generate_content_models()
    selected = choose_gemini_model(available_models=available)

    print(f"Configured GEMINI_MODEL_NAME: {os.getenv('GEMINI_MODEL_NAME', 'auto')}")
    print(f"Selected Gemini model: {selected}")
    print("generateContent modelleri:")
    for model in available:
        print(f"  - {model}")

    if args.skip_generate:
        return 0

    try:
        response = genai.GenerativeModel(selected).generate_content(args.prompt)
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            print("ERROR: Model boş yanıt döndürdü.", file=sys.stderr)
            return 1
        print(f"Smoke test response: {text[:200]}")
        return 0
    except Exception as exc:
        print(f"ERROR: generateContent başarısız ({selected}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
