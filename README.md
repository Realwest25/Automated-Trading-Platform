# Ultimate Trading Platform 2026

Bu proje, MetaTrader 5 (MT5) üzerinde çalışan, Telegram'dan gelen sinyalleri otomatik işleyen, gelişmiş risk yönetimi ve sepet (basket) mantığına sahip bir işlem botudur.

## Özellikler
- **Sinyal İşleme:** Telegram kanallarından (SAFE, AGGRESSIVE, FOREX) gelen sinyalleri anlık okuma.
- **Sepet (Basket) Zekası:** Terste kalan işlemleri aynı TP seviyesinde birleştirerek maksimum kâr hedefleme.
- **DCA Yönetimi:** Sabit pip aralıklarında veya manuel müdahalelerle kademeli işlem desteği.
- **Güvenlik:** API anahtarlarının .env dosyası ile korunması ve otomatik hata yakalama döngüsü.

## Kurulum
1. Gerekli kütüphaneleri kurun: `pip install -r requirements.txt`
2. `.env` dosyanızı oluşturun ve API bilgilerinizi girin.
3. Botu başlatın.
