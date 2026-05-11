import os
import httpx
from loguru import logger

class TelegramProvider:
    """
    Telegram Bildirim Sistemi.
    Kritik krizler veya önemli araştırmalar tamamlandığında mesaj atar.
    """
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.bot_token and self.chat_id)

    async def send_message(self, text: str):
        if not self.enabled:
            logger.debug("Telegram bildirimleri kapalı (Token/ChatID eksik).")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10.0)
                if resp.status_code == 200:
                    logger.info("✅ Telegram bildirimi başarıyla gönderildi.")
                    return True
                else:
                    logger.error(f"❌ Telegram Hatası: {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"❌ Telegram bağlantı hatası: {e}")
            return False

    async def send_crisis_alert(self, topic: str, score: int, advice: str):
        """Önemli bir kriz tespit edildiğinde formatlı mesaj atar."""
        text = (
            f"🚨 <b>KRİTİK DURUM UYARISI</b> 🚨\n\n"
            f"<b>Konu:</b> {topic}\n"
            f"<b>Kriz Skoru:</b> %{score}\n"
            f"<b>Stratejik Tavsiye:</b> {advice}\n\n"
            f"👉 <a href='http://localhost:8000/dashboard'>Dashboard'a Git</a>"
        )
        return await self.send_message(text)
