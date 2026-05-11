FROM python:3.11-slim

# Ortam değişkenleri ve Python optimizasyonları
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ="Europe/Istanbul" \
    PYTHONPATH=/app

WORKDIR /app

# Sistem bağımlılıkları (Veritabanı, timezone ve lxml/pandas gibi ağır kütüphanelerin derlenmesi için)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    curl \
    tzdata \
    && cp /usr/share/zoneinfo/Europe/Istanbul /etc/localtime \
    && echo "Europe/Istanbul" > /etc/timezone \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Bağımlılıkların yüklenmesi
COPY requirements.txt .
# Hataları önlemek için önce pip'i güncelliyor, sonra kütüphaneleri kuruyoruz
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Kodun kopyalanması
COPY . .

# Default command (Docker Compose üzerinden override ediliyor ama burada durması best-practice'dir)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]