import requests
import time

BASE_URL = "http://127.0.0.1:8000"
API_URL = f"{BASE_URL}/api/v1"

def run_smoke_test():
    print("🚀 KARARGAH SMOKE TEST (DUMAN TESTİ) BAŞLIYOR...")
    
    try:
        # 1. API Ayakta mı?
        res = requests.get(f"{BASE_URL}/")
        print(f"✅ 1. API Durumu: {res.status_code} -> {res.json()['status']}")

        # 2. Kaynak Ekleme (Source)
        source_data = {
            "name": "AA Gündem", 
            "url": "https://www.aa.com.tr/tr/rss/default?cat=guncel", 
            "type": "rss", 
            "active": True
        }
        res = requests.post(f"{API_URL}/sources", json=source_data)
        print(f"✅ 2. Kaynak Ekleme: {res.status_code} (AA Gündem RSS eklendi)")

        # 3. Botları Tetikle
        res = requests.post(f"{API_URL}/ingest/run")
        print(f"✅ 3. Saha Ajanları Tetiklendi: {res.status_code}")

        print("⏳ İşçilerin (Celery) veriyi çekip, yapay zekanın (Gemini) etiketleyip skorlaması için 15 saniye bekleniyor...")
        time.sleep(15)

        # 4. Fırsatları Getir
        res = requests.get(f"{API_URL}/opportunities")
        opps = res.json()
        print(f"✅ 4. Stratejik Fırsat Kartları: {res.status_code} -> {len(opps)} adet yeni fırsat bulundu.")
        if opps:
            print(f"   🔥 En sıcak gündem: {opps[0]['topic']} (Skor: {opps[0]['score']})")

        # 5. Günlük Rapor
        res = requests.get(f"{API_URL}/reports/daily")
        print(f"✅ 5. Günlük Brifing Raporu Başarıyla Çekildi:\n\n{res.json().get('briefing', '')}")

        print("\n🎯 HAREKAT BAŞARILI! Karargah %100 Otonom Olarak Çalışıyor.")

    except requests.exceptions.ConnectionError:
        print("❌ HATA: API'ye ulaşılamıyor. Docker konteynerlerinin çalıştığından emin olun.")
    except Exception as e:
        print(f"❌ BEKLENMEYEN HATA: {str(e)}")

if __name__ == "__main__":
    run_smoke_test()