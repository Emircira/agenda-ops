from typing import List, Dict, Any
from datetime import datetime, timedelta
from app.models.core import Opportunity
from app.repositories.content_repository import ContentRepository
from app.repositories.opportunity_repository import OpportunityRepository

class ScoringService:
    def __init__(self):
        # Yönergeye göre belirlenmiş kesin ağırlıklar
        self.weights = {
            "velocity": 0.30,
            "reach": 0.20,
            "relevance": 0.20,
            "ownership": 0.10,
            "time_to_win": 0.10,
            "risk": -0.10 # Risk skoru düşürür (Ceza)
        }

    def calculate_opportunity_score(self, topic: str, frame: str, items: List[Dict[str, Any]]) -> dict:
        """Birden fazla içeriği sentezleyerek o gündem maddesi için genel fırsat skoru hesaplar."""
        if not items:
            return {"score": 0.0, "rationale": {}}

        content_count = len(items)
        
        total_velocity = 0
        total_reach = 0
        high_risk_count = 0
        support_count = 0

        # Kümeye ait tüm içeriklerin metriklerini topla
        for item in items:
            # 1. Velocity (Hız/Tazelik): İçerik ne kadar yeniyse o kadar yüksek (Maks 100)
            pub_date = item.get("published_at", datetime.utcnow())
            age_hours = (datetime.utcnow() - pub_date).total_seconds() / 3600
            velocity_score = max(0, 100 - (age_hours * 2)) # 50 saatte sıfırlanır
            total_velocity += velocity_score

            # 2. Reach (Erişim/Etkileşim): YouTube views veya Twitter likes üzerinden
            reach_score = 10 # Default
            metrics = item.get("raw_json") or {}
            platform = item.get("platform", "")
            
            if platform == "youtube":
                views = int(metrics.get("views", 0)) if isinstance(metrics, dict) else 0
                reach_score = min(100, (views / 10000) * 100)
            elif platform in ["x", "twitter"]:
                likes = int(metrics.get("favorite_count", metrics.get("likes", 0))) if isinstance(metrics, dict) else 0
                reach_score = min(100, (likes / 1000) * 100)
            total_reach += reach_score

            # Risk & Stance Sayımları
            if item.get("risk_level") == "high":
                high_risk_count += 1
            if item.get("stance") == "support":
                support_count += 1

        # Küme Ortalamaları
        avg_velocity = total_velocity / content_count
        avg_reach = total_reach / content_count

        # 3. Relevance (İlgililik): Konunun önemi
        relevance_score = 80 if topic in ["Ekonomi", "Seçim", "Güvenlik"] else 50

        # 4. Ownership (Sahiplik): Siyasi hedefin konuya hakimiyeti
        ownership_score = 70 if (support_count / content_count) > 0.5 else 40

        # 5. Time to Win: Krizin çözülme/aksiyon alma hızı potansiyeli
        time_to_win_score = 90 if frame == "Kriz" else 50

        # 6. Risk: Küme geneli risk seviyesi (Ağırlıklı)
        risk_ratio = high_risk_count / content_count
        risk_score = 90 if risk_ratio > 0.5 else (50 if risk_ratio > 0.2 else 20)

        # TOTAL HESAPLAMA
        total_score = (
            (avg_velocity * self.weights["velocity"]) +
            (avg_reach * self.weights["reach"]) +
            (relevance_score * self.weights["relevance"]) +
            (ownership_score * self.weights["ownership"]) +
            (time_to_win_score * self.weights["time_to_win"]) +
            (risk_score * self.weights["risk"])
        )

        final_score = max(0, min(100, total_score)) # 0-100 arası kelepçele

        # Özet Derlemesi (İçeriklerden gelen özetleri birleştir)
        summaries = [i.get("summary", "") for i in items if i.get("summary")]
        combined_summary = ". ".join(summaries[:3]) + ("..." if len(summaries) > 3 else "")

        rationale = {
            "breakdown": {
                "velocity": round(avg_velocity, 1),
                "reach": round(avg_reach, 1),
                "relevance": relevance_score,
                "ownership": ownership_score,
                "time_to_win": time_to_win_score,
                "risk_penalty": risk_score
            },
            "insight": combined_summary if combined_summary else f"Bu fırsat kartı {content_count} adet kaynağın sentezlenmesiyle oluşturuldu."
        }

        return {
            "score": round(final_score, 1),
            "rationale": rationale
        }

    async def generate_opportunities(self, db, window_hours=24):
        """Etiketlenmiş içerikleri gruplar ve her grup için fırsat skoru hesaplayıp kaydeder."""
        content_repo = ContentRepository(db)
        opportunity_repo = OpportunityRepository(db)

        await opportunity_repo.delete_all()
        time_threshold = datetime.utcnow() - timedelta(hours=window_hours)
        
        pairs = await content_repo.list_labeled_content_pairs_since(time_threshold)
        
        if not pairs:
            return []

        # Topic ve Frame'e göre grupla
        groups = {}
        for content, label in pairs:
            key = (label.topic, label.frame)
            if key not in groups:
                groups[key] = []
            
            # İçeriği ve etiketi birleştirip servisin anladığı formata sok
            item_data = {
                "id": str(content.id),
                "platform": content.platform,
                "published_at": content.published_at,
                "text": content.text,
                "raw_json": content.raw_json,
                "topic": label.topic,
                "frame": label.frame,
                "stance": label.stance,
                "risk_level": label.risk_level
            }
            groups[key].append(item_data)

        opportunities_created = []
        for (topic, frame), items in groups.items():
            analysis = self.calculate_opportunity_score(topic, frame, items)
            
            # Yeni Fırsat Kartı oluştur
            opp = Opportunity(
                topic=topic,
                frame=frame,
                score=analysis["score"],
                parts={"count": len(items), "content_ids": [i["id"] for i in items]},
                rationale=analysis["rationale"],
                window_hours=window_hours
            )
            opportunities_created.append(opp)
        
        await opportunity_repo.persist_opportunities(opportunities_created)
        return opportunities_created
