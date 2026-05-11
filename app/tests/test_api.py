from fastapi.testclient import TestClient
from app.main import app

# Karargah'ın API motorunu test ortamına alıyoruz
client = TestClient(app)

def test_read_root():
    """API'nin ana dizininin ve ayakta olduğunun testi"""
    response = client.get("/")
    assert response.status_code == 200
    assert "Karargah" in response.json()["status"]

def test_api_docs_available():
    """Swagger UI dokümantasyonunun dışa açık olduğunun testi"""
    response = client.get("/docs")
    assert response.status_code == 200

# Not: Veritabanı (PostgreSQL) gerektiren uç noktaların (örn: /api/v1/sources) 
# testleri MVP aşamasından sonra izole bir test veritabanı (SQLite_memory) ile yapılacaktır.