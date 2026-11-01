# ✈️ Hopper Flight Price Scraper & Intelligence Tracker (Python + Playwright)

نظام سكرابر بايثون متكامل مبني بالكامل على **Python 3** و **Playwright**:
- يفتح متصفح Google Chrome الحقيقي أمامك على الشاشة (Headed Mode) لتشاهد العملية بعينيك.
- يتخطى نظام حماية Cloudflare و Google reCAPTCHA Enterprise برمجياً وديناميكياً بدون كوكيز منتهية الصلاحية.
- يسحب بيانات حقيقية 100% مباشرة من هوبر (الأسعار الحقيقية، شركات الطيران، أوقات الإقلاع والوصول، التوقفات، وفئات المقاعد).
- يقوم بحفظ وتحديث الرحلات تلقائياً وبدقة في قاعدة بيانات Supabase (`one_way_flights` و `round_trip_flights`).

---

### 🚀 كيفية التشغيل (Usage)

#### 1. سحب رحلة محددة (مسار وتاريخ):
```bash
python run.py --origin ADD --dest DXB --date 2026-11-05
```
أو لمسار ميلانو إلى القاهرة:
```bash
python run.py --origin MIL --dest CAI --date 2026-11-05
```

#### 2. سحب شهر كامل (30 يوماً متتالية):
```bash
python run.py --origin ADD --dest DXB --date 2026-11-02 --days 30
```

#### 3. التشغيل التفاعلي (Interactive Mode):
فقط اكتب:
```bash
python run.py
```
وستظهر لك قائمة خيارات لاختيار المسار أو إدخال مسار مخصص بسهولة.

#### 4. عرض حالة قاعدة البيانات والرحلات المخزنة في Supabase:
```bash
python run.py --status
```

#### 5. مسح البيانات القديمة من Supabase:
```bash
python run.py --clear
```

---

### 📁 هيكل المشروع النظيف:
- `hopper_scraper.py`: محرك السكرابر الأساسي المعتمد على Playwright و CDP.
- `database.py`: اتصال وقراءة وحفظ البيانات في Supabase PostgreSQL بدقة (1 Cheapest flight per date).
- `routes.py`: قائمة أفضل 250 مسار طيران عالمي مع تصنيف المدن والمطارات.
- `run.py`: واجهة التشغيل السريعة بالأوامر والتفاعل المباشر.

