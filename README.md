# 🔗 نظام أتمتة روابط الإحالة

نظام متكامل لأتمتة نشر روابط الإحالة من أمازون، يعمل على مدار الساعة مع لوحة تحكم كاملة باللغة العربية.

## ✨ المميزات

- **جلب الترندات** من TikTok وInstagram عبر Apify
- **البحث التلقائي** في أمازون (SA/AE) مع رابط إحالة مباشر
- **توليد المحتوى** بالذكاء الاصطناعي (Claude / GPT / Gemini / Groq)
- **النشر التلقائي** على Telegram + WhatsApp + X
- **لوحة تحكم عربية** كاملة بتصميم Apple-style

## 🚀 تشغيل المشروع

```bash
# 1. تثبيت المتطلبات
pip install -r backend/requirements.txt

# 2. نسخ ملف البيئة
cp .env.example .env

# 3. تشغيل الخادم
python -m uvicorn backend.main:app --reload --port 8000

# 4. افتح المتصفح
# http://localhost:8000
```

## 📁 هيكل المشروع

```
affiliate-system/
├── backend/
│   ├── main.py          # FastAPI — نقطة الدخول
│   ├── config.py        # قاعدة البيانات + التشفير
│   ├── models.py        # نماذج SQLAlchemy
│   ├── scheduler.py     # APScheduler
│   └── services/
│       ├── trend_service.py   # جلب الترندات (Apify)
│       ├── amazon_service.py  # Amazon PA API
│       ├── ai_service.py      # توليد المحتوى (Multi-LLM)
│       ├── publisher.py       # النشر على القنوات
│       └── tracker.py         # تتبع الإحصاءات
├── frontend/
│   ├── index.html       # SPA الرئيسي
│   ├── pages/           # صفحات التطبيق
│   └── components/      # مكونات مشتركة
├── database/            # SQLite
├── .env.example
└── render.yaml          # نشر على Render
```

## 🔑 إعداد مفاتيح API

بعد التشغيل، افتح `http://localhost:8000` وانتقل لصفحة **مفاتيح API** لإضافة:

| الخدمة | الوصف |
|--------|-------|
| Anthropic | لتوليد المحتوى بـ Claude |
| OpenAI | بديل GPT-4o |
| Google | بديل Gemini |
| Groq | أسرع نموذج (مجاني) |
| Amazon Associates | PA API للبحث والروابط |
| Apify | جلب بيانات TikTok/Instagram |
| Telegram Bot | النشر على القناة |
| UltraMsg (WhatsApp) | النشر على واتساب |
| X API | النشر على تويتر |

## 🌐 النشر على Render

1. ارفع المشروع على GitHub
2. سجّل في [Render.com](https://render.com)
3. أنشئ Web Service جديد من المستودع
4. Render سيقرأ `render.yaml` تلقائياً

## ⚙️ المرونة

- جميع الإعدادات قابلة للتعديل من لوحة التحكم
- مفاتيح API مشفّرة بـ Fernet في SQLite
- fallback تلقائي بين نماذج LLM
- جدولة مرنة (صباح / ظهر / مساء)

## 🔐 الأمان

- لا تُحفظ المفاتيح في أي ملف نصي
- كل مفتاح مشفّر قبل الحفظ في قاعدة البيانات
- الواجهة تعرض فقط آخر 4 أحرف من كل مفتاح
