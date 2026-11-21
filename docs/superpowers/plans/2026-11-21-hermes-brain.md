# Hermes Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** جعل Hermes Agent (محلي) العقل الوحيد للمنصة: توليد النصوص، اختيار العروض، اختيار التصنيفات، مع واجهة تحكم له، وإيقاف تلقائي عند تعطله، وإيقاف يدوي من تيليجرام، وتشغيل المنصة على ويندوز.

**Architecture:** عميل واحد (`hermes_client`) لواجهة متوافقة مع OpenAI يحل محل كل مزودي `ai_service.py`. أي فشل في Hermes يرمي `HermesUnavailable` فيوقف النشر عبر علم في الإعدادات (`safety`) ويُنبّه المالك (`owner_bot`). كل نشر يمر عبر `publish_product` الذي يفحص الإيقاف والحد اليومي وصحة المنشور قبل الإرسال. واجهة `/api/hermes/*` بمفتاح خاص وعلى localhost فقط.

**Tech Stack:** Python 3.11+، FastAPI، SQLAlchemy (SQLite)، APScheduler، aiohttp (موجودة)، pytest + httpx للاختبار، PowerShell + Task Scheduler لويندوز.

**Spec:** `docs/superpowers/specs/2026-11-20-hermes-brain-design.md`

## Global Constraints

- لا OpenRouter ولا أي مزوّد ذكاء اصطناعي سحابي بديل؛ يُحذف الكود والمفاتيح والواجهات الخاصة بها.
- عند فشل Hermes (بعد محاولتين): يتوقف النشر تلقائياً + تنبيه تيليجرام **مرة واحدة لكل انقطاع** + لا قوالب ثابتة.
- الإيقاف التلقائي (`publish_paused_hermes`) منفصل عن اليدوي (`publish_paused_manual`)؛ اليدوي لا يُرفع إلا بـ `/resume` من المالك.
- الأوامر تُقبل من `OWNER_TELEGRAM_ID` فقط: `/stop` و`/resume` و`/status`.
- السعر والرابط يُؤخذان من بيانات المنتج لا من نص Hermes؛ حد يومي افتراضي `max_posts_per_day = 12`.
- Hermes لا يصل إلى مفاتيح API ولا توكنات النشر عبر واجهة التحكم.
- واجهة التحكم: ترويسة `X-Hermes-Key` تطابق `HERMES_CONTROL_KEY`، والطلب من `127.0.0.1` أو `::1` فقط.
- متغيرات البيئة الجديدة: `HERMES_BASE_URL`، `HERMES_API_KEY`، `HERMES_MODEL`، `HERMES_CONTROL_KEY`، `OWNER_TELEGRAM_ID`.
- النظام المستهدف ويندوز؛ خدمة دائمة عبر Task Scheduler (قرار المنفّذ: لا اعتماد على أدوات خارجية مثل NSSM).
- أسماء الدوال العامة في `ai_service.py` (`generate_content`، `generate_deal_of_day_content`، `call_ai`) تبقى حتى لا ينكسر المستدعون.
- نص المنتجات والرسائل للمستخدم بالعربية؛ الأكواد والمعرفات بالإنجليزية.

## Review Focus

1. Hermes يرد بنص سليم الشكل لكن بسعر مختلف أو بلا رابط الإحالة أو برابط غريب: لا يُنشر، وتصبح الحالة `rejected` (Task 6).
2. Hermes يرد JSON صالحاً لكن بأرقام خارج النطاق أو مكررة أو بمعرّفات تصنيف غير موجودة: يُعامل كفشل (إيقاف + تنبيه) لا كنجاح جزئي (Task 4).
3. الإيقاف يحدث أثناء دورة autopilot وفي الطابور منتجات معتمدة: لا شيء منها يُنشر أثناء الإيقاف وتبقى `approved` ثم تُنشر بعد الاستئناف (Task 6).
4. رسالة `/stop` من غير المالك أو من مجموعة: تُتجاهل بصمت؛ و`/stop` يبقى بعد إعادة تشغيل المنصة (Task 2).
5. واجهة التحكم: مفتاح خاطئ أو طلب غير محلي يُرفض؛ ومحاولة Hermes تعديل مفتاح API أو رفع الإيقاف اليدوي تُرفض (Task 7).

---

### Task 1: بنية الاختبار وعميل Hermes

**Files:**
- Create: `requirements-dev.txt` (يحوي `pytest` و`httpx`)
- Create: `tests/conftest.py`
- Create: `backend/services/hermes_client.py`
- Test: `tests/test_hermes_client.py`

**Interfaces:**
- Produces:
  - `class HermesUnavailable(RuntimeError)`
  - `async def chat(prompt: str, system: str = "", *, timeout: float | None = None) -> str`
  - `async def check_health() -> bool`
  - `def hermes_model() -> str`
  - `async def _post_chat(payload: dict, timeout: float) -> dict` (نقطة الحقن في الاختبارات؛ تستخدم aiohttp)
- fixture `conftest.py::db` : يضبط `DATABASE_URL=sqlite:///<tmp>` قبل أي استيراد لـ `backend.config`، ويستدعي `init_db()`، ويفرغ جدول الإعدادات بين الاختبارات ويستدعي `_invalidate_settings_cache()`.

- [ ] **Step 1: اكتب الاختبارات الفاشلة.** `test_chat_returns_text` (يحقن `_post_chat` ليعيد `{"choices":[{"message":{"content":"نص"}}]}` فيكون الناتج `"نص"`)، `test_chat_retries_once_then_succeeds` (أول استدعاء يرمي `OSError` والثاني ينجح ⇒ استدعيان بالضبط)، `test_chat_raises_after_two_failures` (استدعيان ثم `HermesUnavailable`)، `test_chat_empty_reply_is_failure` (محتوى فارغ أو أقل من 5 محارف ⇒ `HermesUnavailable`)، `test_missing_base_url_raises` (بدون `HERMES_BASE_URL` ⇒ `HermesUnavailable` بدون أي طلب)، `test_check_health_false_on_error`.
- [ ] **Step 2: شغّل** `pytest tests/test_hermes_client.py -v` — المتوقع: فشل (الوحدة غير موجودة).
- [ ] **Step 3: نفّذ `hermes_client.py`.** `POST {HERMES_BASE_URL}/chat/completions` ترويسة `Authorization: Bearer <get_api_key("hermes_api_key")>` والجسم `{"model": hermes_model(), "messages":[system?, user]}`؛ المهلة الافتراضية 120 ث (`HERMES_TIMEOUT` اختياري)؛ محاولتان كحد أقصى؛ `check_health` طلب `GET {base}/models` بمهلة 5 ث. أضف إلى `config.ENV_VAR_MAP` المفاتيح `hermes_api_key` و`hermes_control_key` و`owner_telegram_id` مع متغيراتها.
- [ ] **Step 4: شغّل الاختبارات** — المتوقع: نجاح الكل.
- [ ] **Step 5: Commit** — `feat(hermes): add Hermes chat client and test harness`

### Task 2: حالة الإيقاف وحواجز المنشور (`safety.py`)

**Files:**
- Create: `backend/services/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Consumes: `get_setting` / `set_setting` من `backend.config`.
- Produces:
  - `def is_paused() -> tuple[bool, str]` — السبب `"manual"` أو `"hermes"` أو `""` (اليدوي له الأولوية)
  - `def set_manual_pause(paused: bool) -> None` ، `def set_hermes_pause(paused: bool) -> bool` (يعيد `True` إذا تغيّرت الحالة فعلاً)
  - `def posts_today() -> int` ، `def daily_cap_reached() -> bool`
  - `class PostRejected(ValueError)` ، `def validate_post(content: str, product: dict) -> None`

- [ ] **Step 1: اكتب الاختبارات الفاشلة.**
  - `test_pause_flags_and_priority`: يدوي + hermes معاً ⇒ `(True, "manual")`؛ رفع اليدوي ⇒ `(True, "hermes")`؛ رفعهما ⇒ `(False, "")`.
  - `test_manual_pause_survives_cache_reset`: بعد `_invalidate_settings_cache()` يبقى الإيقاف.
  - `test_set_hermes_pause_reports_transition`: `True` ثم `False` عند التكرار.
  - `test_daily_cap`: `max_posts_per_day=2` وإدخال منتجين `published` بتاريخ اليوم ⇒ `daily_cap_reached()`؛ منتج بتاريخ الأمس لا يُحسب.
  - `test_validate_post_ok`: محتوى يحوي `affiliate_url` والسعر `79.99` ⇒ لا استثناء.
  - `test_validate_post_missing_link`، `test_validate_post_wrong_price` (محتوى بسعر 59 والمنتج 79.99)، `test_validate_post_foreign_url` (رابط غير رابط الإحالة ولا رابط قناة التيليجرام من `telegram_footer`)، `test_validate_post_arabic_digits_price` (السعر بأرقام عربية هندية `٧٩٫٩٩` يُقبل)، `test_validate_post_zero_price_rejected`.
- [ ] **Step 2: شغّل** `pytest tests/test_safety.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ `safety.py`.** مفاتيح الإعدادات: `publish_paused_manual`، `publish_paused_hermes`، `max_posts_per_day` (افتراضي 12). `posts_today` يعد `Product.status == "published"` و`published_at` ≥ بداية اليوم. `validate_post`: يطبّع الأرقام العربية الهندية والفاصلة العشرية العربية إلى ASCII، ويستخرج كل الأرقام، ويشترط ظهور `price_current` (بصيغة `f"{x:g}"`) بينها وأن يكون > 0، ويشترط وجود `affiliate_url`، ويرفض أي رابط `http(s)` غير الرابطين المسموحين.
- [ ] **Step 4: شغّل الاختبارات** — المتوقع: نجاح.
- [ ] **Step 5: Commit** — `feat(safety): pause flags, daily cap and post validation`

### Task 3: بوت المالك (تنبيهات وأوامر تيليجرام)

**Files:**
- Create: `backend/services/owner_bot.py`
- Test: `tests/test_owner_bot.py`

**Interfaces:**
- Consumes: `safety.set_manual_pause`، `safety.is_paused`، `safety.posts_today`، `get_api_key("telegram_token")`، `get_api_key("owner_telegram_id")`.
- Produces:
  - `async def send_owner_alert(text: str) -> bool` — يرسل رسالة خاصة إلى `OWNER_TELEGRAM_ID` (`chat_id` = معرّف المالك)؛ `False` بلا استثناء عند غياب الإعدادات أو الفشل
  - `def handle_command(text: str, from_id: int, chat_type: str = "private") -> str | None` — يعيد نص الرد أو `None` للتجاهل
  - `async def poll_commands(stop: asyncio.Event) -> None` — حلقة `getUpdates` طويلة المدة (مهلة 30 ث) تحفظ `offset` في الذاكرة وتنتهي عند `stop`
  - `async def _telegram_api(method: str, payload: dict) -> dict` (نقطة الحقن؛ aiohttp)

- [ ] **Step 1: اكتب الاختبارات الفاشلة.**
  - `test_stop_sets_manual_pause` : `handle_command("/stop", OWNER)` ⇒ `is_paused() == (True,"manual")` والرد يحوي "أُوقف".
  - `test_resume_clears_manual_only`: مع إيقاف hermes أيضاً ⇒ بعد `/resume` يبقى `(True,"hermes")` والرد يوضح ذلك.
  - `test_status_reply_mentions_state_and_count`.
  - `test_non_owner_ignored` : `from_id` مختلف ⇒ `None` ولا تغيير في الحالة.
  - `test_group_chat_ignored` : `chat_type="group"` من المالك نفسه ⇒ `None`.
  - `test_unknown_command_returns_none`.
  - `test_send_owner_alert_without_config_returns_false`.
  - `test_poll_commands_replies_to_owner` : يحقن `_telegram_api` بتحديث واحد من المالك ثم `stop.set()` ⇒ استُدعي `sendMessage` مرة بنص الرد.
- [ ] **Step 2: شغّل** `pytest tests/test_owner_bot.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ `owner_bot.py`** بنصوص عربية قصيرة للردود. `poll_commands` يتجاهل الأخطاء الشبكية وينتظر 5 ث قبل المحاولة التالية.
- [ ] **Step 4: شغّل الاختبارات** — المتوقع: نجاح.
- [ ] **Step 5: Commit** — `feat(owner-bot): Telegram alerts and /stop /resume /status`

### Task 4: انتقالات صحة Hermes ومراقبه

**Files:**
- Create: `backend/services/hermes_health.py`
- Test: `tests/test_hermes_health.py`

**Interfaces:**
- Consumes: `safety.set_hermes_pause`، `owner_bot.send_owner_alert`، `hermes_client.check_health`.
- Produces:
  - `async def report_failure(reason: str) -> None` — يوقف النشر، ويرسل تنبيهاً **فقط عند الانتقال** من سليم إلى معطل
  - `async def report_success() -> None` — إن كان الإيقاف بسبب hermes: يرفعه ويرسل «عاد Hermes للعمل»
  - `async def watchdog_tick() -> None` — إن كان موقوفاً بسبب hermes: ينادي `check_health()` وعند النجاح `report_success()`

- [ ] **Step 1: اكتب الاختبارات الفاشلة** (مع حقن `send_owner_alert` وعدّه): `test_first_failure_pauses_and_alerts_once`، `test_repeated_failures_alert_once` (ثلاثة استدعاءات ⇒ تنبيه واحد)، `test_success_after_failure_resumes_and_alerts` ، `test_success_when_healthy_is_silent`، `test_watchdog_noop_when_not_paused` (لا يستدعي `check_health`)، `test_watchdog_resumes_when_healthy`، `test_watchdog_stays_paused_when_still_down`، `test_manual_pause_not_cleared_by_recovery`.
- [ ] **Step 2: شغّل** `pytest tests/test_hermes_health.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ الوحدة** مستخدماً القيمة المعادة من `set_hermes_pause` لتحديد الانتقال. نص التنبيه يحوي `reason` مقتطعاً إلى 200 حرف.
- [ ] **Step 4: شغّل الاختبارات** — المتوقع: نجاح.
- [ ] **Step 5: Commit** — `feat(hermes): outage detection, alert-once and recovery watchdog`

### Task 5: إعادة كتابة `ai_service.py` على Hermes

**Files:**
- Modify: `backend/services/ai_service.py` (حذف المزودين والاكتشاف والنماذج الميتة و`SUPPORTED_MODELS` و`_make_fallback*`؛ إبقاء القوالب `SYSTEM_PROMPT` و`USER_PROMPT_TEMPLATE` ودوال المساعدة للتقييم والعروض والفوتر والكاش)
- Modify: `backend/services/trend_service.py` (`extract_product_name_with_ai` عبر `call_ai` بدل `anthropic`، وعند `HermesUnavailable` يعيد `caption[:80]` كما يفعل عند غياب المفتاح)
- Test: `tests/test_ai_service.py`

**Interfaces:**
- Consumes: `hermes_client.chat`، `hermes_client.HermesUnavailable`، `hermes_health.report_failure/report_success`.
- Produces:
  - `async def call_ai(prompt: str, system: str = "", model_id: str = "auto") -> str` — يرمي `HermesUnavailable` بعد `report_failure`؛ ينادي `report_success` عند النجاح
  - `async def call_ai_json(prompt: str, system: str = "") -> dict` — يستخرج أول كائن JSON؛ غير صالح ⇒ `report_failure` ثم `HermesUnavailable`
  - `async def generate_content(product: dict, llm: str = "auto") -> tuple[str, str]` و`generate_deal_of_day_content(...)` بنفس الشكل؛ المعامل الثاني اسم نموذج Hermes؛ `llm` يُهمل
  - `async def get_available_models() -> list`، `async def test_model(model_id: str) -> dict`، `async def test_generate_live(model_id: str, product: dict | None = None) -> dict`، `async def diagnose_ai() -> dict` (تبقى لنقاط الاختبار في `main.py`؛ تعكس حالة Hermes)

- [ ] **Step 1: اكتب الاختبارات الفاشلة** (حقن `hermes_client.chat`): `test_generate_content_returns_text_and_model` ، `test_generate_content_uses_cache_by_asin` (نداء ثانٍ لنفس ASIN لا يستدعي `chat`)، `test_failure_raises_and_reports` (يرمي `HermesUnavailable` ويكون `is_paused() == (True,"hermes")`)، `test_success_after_outage_resumes`، `test_call_ai_json_extracts_object_from_noisy_text`، `test_call_ai_json_invalid_reports_failure`، `test_no_fallback_text_on_failure` (لا يعود أي نص قالب).
- [ ] **Step 2: شغّل** `pytest tests/test_ai_service.py -v` — المتوقع: فشل.
- [ ] **Step 3: أعد كتابة الملف** على الواجهة أعلاه (هدف الحجم أقل من 300 سطر). لا تعدّل نصوص القوالب العربية.
- [ ] **Step 4: عالج المستدعين** : `main.py` (`_regenerate_for_product`، التوليد اليدوي، `gen_one`) يحوّل `HermesUnavailable` إلى `HTTPException(503, "Hermes غير متاح — تم إيقاف النشر")`؛ `scheduler._process_single_trend` يتخطى المنتج ويسجّل؛ `auto_discover_and_queue` و`run_autopilot_slot` يوقفان الدورة كاملة عند `HermesUnavailable` بدل الاستمرار منتجاً بمنتج.
- [ ] **Step 5: شغّل** `pytest -v` — المتوقع: نجاح الكل.
- [ ] **Step 6: Commit** — `feat(ai): replace OpenRouter chain with Hermes client`

### Task 6: اختيار العروض والتصنيفات، وحماية النشر

**Files:**
- Modify: `backend/services/deals_finder_service.py` (`_ai_select_top5`، إضافة `pick_categories_with_hermes`)
- Modify: `backend/services/publisher.py` (`publish_product`)
- Modify: `backend/scheduler.py` (`run_autopilot_slot` يستخدم `pick_categories_with_hermes`؛ `publish_approved_products` و`fetch_and_queue` يتخطيان عند الإيقاف)
- Test: `tests/test_selection.py`، `tests/test_publish_guard.py`

**Interfaces:**
- Consumes: `ai_service.call_ai_json`، `hermes_health.report_failure`، `safety.is_paused/daily_cap_reached/validate_post/PostRejected`.
- Produces:
  - `async def _ai_select_top5(products: list[dict], category_name: str) -> list[dict]` — بنفس التوقيع؛ يطلب `{"selected":[n,...], "reason":"..."}`؛ الأرقام يجب أن تكون ضمن 1..N وبلا تكرار وعددها 3 إلى 5 وإلا ⇒ `report_failure` + `HermesUnavailable` (لا عودة للترتيب الحسابي)
  - `async def pick_categories_with_hermes(n: int) -> list[dict]` — المرشحون من `pick_unused_categories_for_today(10_000)`؛ يطلب `{"selected":["cat_id",...], "reason":"..."}`؛ المعرّفات يجب أن تكون ضمن المرشحين وبلا تكرار وعددها 1..n وإلا فشل كما سبق؛ الإرجاع بنفس شكل القواميس (`id, cat_id, name_ar, icon, keywords`)؛ قائمة مرشحين فارغة ⇒ `[]` دون نداء Hermes
  - `publish_product` يعيد `{"success": False, "skipped": True, "reason": "paused:<manual|hermes>" | "daily_cap"}` دون تغيير حالة المنتج؛ وعند `PostRejected` يضع الحالة `rejected` ويسجّل السبب في `PublishLog` ويعيد `{"success": False, "rejected": True, "error": str}`

- [ ] **Step 1: اكتب الاختبارات الفاشلة.**
  - `test_select_top5_returns_chosen_in_order` (حقن `call_ai_json` ⇒ `{"selected":[3,1,2]}`).
  - `test_select_top5_short_list_skips_hermes` (5 منتجات أو أقل ⇒ تُعاد كما هي بلا نداء).
  - `test_select_top5_out_of_range_is_failure` ، `test_select_top5_duplicates_is_failure` ، `test_select_top5_too_few_is_failure` — كلها ترمي `HermesUnavailable` ويصبح `is_paused()` hermes.
  - `test_pick_categories_valid`، `test_pick_categories_unknown_id_is_failure`، `test_pick_categories_empty_pool_no_call`.
  - `test_discover_deals_propagates_hermes_failure`.
  - `test_publish_skipped_when_paused_keeps_status_approved` (يدوي ثم hermes)، `test_publish_skipped_at_daily_cap`، `test_publish_rejects_wrong_price` (الحالة `rejected` وlog مكتوب ولا نداء لقنوات النشر)، `test_publish_ok_calls_channels` (حقن `publish_to_telegram`).
  - `test_autopilot_aborts_slot_on_hermes_failure` : لا منتجات جديدة تُحفظ ولا نشر.
  - `test_pause_mid_slot_leaves_remaining_approved` : تُنشر الأولى، ثم يُفعَّل الإيقاف، فتبقى البقية `approved`.
- [ ] **Step 2: شغّل** `pytest tests/test_selection.py tests/test_publish_guard.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ** الدوال أعلاه. `publish_product` يفحص بالترتيب: الإيقاف، الحد اليومي، `validate_post` بعد تحميل بيانات المنتج (يحتاج `price_current` و`affiliate_url` من الصف). في `run_autopilot_slot` استبدل `pick_unused_categories_for_today` بـ `pick_categories_with_hermes` وأبقِ `mark_categories_used`.
- [ ] **Step 4: شغّل** `pytest -v` — المتوقع: نجاح الكل.
- [ ] **Step 5: Commit** — `feat(selection,publish): Hermes picks deals/categories; guarded publishing`

### Task 7: واجهة تحكم Hermes

**Files:**
- Create: `backend/hermes_api.py`
- Modify: `backend/main.py` (تضمين الـ router؛ إضافة `"/api/hermes/"` إلى `_PUBLIC_PREFIXES` لأن المصادقة تتم داخل الـ router بمفتاح لا بكوكي)
- Test: `tests/test_hermes_api.py`

**Interfaces:**
- Consumes: `safety`، `scheduler.run_autopilot_slot`، `publisher.publish_product`، `get_setting/set_setting`، `Product`.
- Produces: `router = APIRouter(prefix="/api/hermes")` بتبعية `require_hermes_key` (401 لمفتاح خاطئ/مفقود، 403 لعميل غير محلي، 503 إن لم يُضبط `HERMES_CONTROL_KEY`) ونقاط:
  - `GET /status` ⇒ `{paused, reason, posts_today, daily_cap, queue: {pending, approved}}`
  - `GET /queue?status=pending|approved&limit=50` ⇒ قائمة منتجات مختصرة
  - `POST /run-slot` ⇒ يشغّل `run_autopilot_slot` في الخلفية
  - `POST /publish/{product_id}` ⇒ نتيجة `publish_product`
  - `POST /approve/{product_id}` و`POST /reject/{product_id}`
  - `POST /pause` ⇒ `set_manual_pause(True)`؛ **لا توجد** نقطة لرفع الإيقاف اليدوي
  - `GET /settings` و`PUT /settings` ⇒ ضمن قائمة مسموحة ثابتة `HERMES_EDITABLE_SETTINGS` (`min_discount_pct`، `min_rating`، `posts_per_slot`، `post_interval_minutes`، `autopilot_enabled`، `autopilot_cats_per_slot`، `autopilot_products_per_cat`، `autopilot_dedup_hours`، `max_posts_per_day`)؛ أي مفتاح آخر ⇒ 400

- [ ] **Step 1: اكتب الاختبارات الفاشلة** (`TestClient` على `app` مع `client=("127.0.0.1", 5000)` كما يدعم Starlette): `test_missing_key_401`، `test_wrong_key_401`، `test_non_local_client_403`، `test_key_not_configured_503`، `test_status_ok`، `test_queue_filters_by_status`، `test_pause_sets_manual`، `test_no_resume_endpoint` (POST `/api/hermes/resume` ⇒ 404)، `test_settings_allowlist_rejects_api_key_names` (`telegram_token` وأي مفتاح خارج القائمة ⇒ 400)، `test_settings_update_allowed_key`، `test_publish_respects_pause` (نتيجة `skipped`)، `test_cookie_not_required_but_other_api_still_needs_it` (`/api/health` عام، و`/api/products` بلا كوكي ⇒ 401 كما اليوم).
- [ ] **Step 2: شغّل** `pytest tests/test_hermes_api.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ الـ router** ومقارنة المفتاح بـ `secrets.compare_digest`.
- [ ] **Step 4: شغّل الاختبارات** — المتوقع: نجاح.
- [ ] **Step 5: Commit** — `feat(api): Hermes control router with key auth and allowlist`

### Task 8: ربط المجدول والتشغيل (مراقب Hermes وبوت المالك)

**Files:**
- Modify: `backend/scheduler.py` (`setup_scheduler`: مهمة `hermes_watchdog` كل دقيقتين تنادي `hermes_health.watchdog_tick`)
- Modify: `backend/main.py` (`lifespan`: يبدأ `owner_bot.poll_commands` كمهمة خلفية بـ `asyncio.Event` ويوقفها عند الإغلاق؛ نقاط الاختبار تبقى لكن مفتاح `openrouter` في `/api/test-key` يُستبدل بـ `hermes_api_key` ويستدعي `ai_service.test_model`)
- Test: `tests/test_wiring.py`

**Interfaces:**
- Consumes: Tasks 3 و4 و5.

- [ ] **Step 1: اكتب الاختبارات الفاشلة.** `test_scheduler_registers_watchdog_job` (بعد `setup_scheduler()` يوجد job بمعرّف `hermes_watchdog` وفاصل 120 ث؛ أوقف الـ scheduler في الإنهاء)، `test_lifespan_starts_and_stops_owner_poller` (حقن `poll_commands` يسجّل البدء والإيقاف)، `test_test_key_hermes_branch` (حقن `test_model`).
- [ ] **Step 2: شغّل** `pytest tests/test_wiring.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ الربط.**
- [ ] **Step 4: شغّل** `pytest -v` — المتوقع: نجاح الكل.
- [ ] **Step 5: Commit** — `feat(runtime): wire watchdog and owner command poller`

### Task 9: تنظيف OpenRouter وواجهة الإعدادات

**Files:**
- Modify: `backend/config.py` (حذف `openrouter` و`groq` و`openai` و`google` و`anthropic` من `ENV_VAR_MAP`؛ حذف `primary_llm` و`llm_fallback` و`llm_priority` و`llm_priority_version` و`_DEPRECATED_MODELS` ومنطق ترحيلها من `DEFAULT_SETTINGS` و`init_default_settings`؛ إضافة الافتراضي `max_posts_per_day: 12`)
- Modify: `backend/requirements.txt` و`requirements.txt` (حذف `anthropic` و`openai` و`google-generativeai` و`groq`)
- Modify: `frontend/pages/api-keys.html` (بطاقة OpenRouter ⇒ بطاقة «Hermes» بحقل `hermes_api_key`، وأضف بطاقة معرّف المالك `owner_telegram_id`)
- Modify: `frontend/pages/settings.html` (حذف قائمة اختيار النموذج)
- Modify: `frontend/pages/ai-test.html` (قائمة المزودين تصير Hermes فقط)
- Test: `tests/test_config_cleanup.py`

- [ ] **Step 1: اكتب الاختبارات الفاشلة.** `test_no_openrouter_references_in_backend` (قراءة ملفات `backend/**/*.py` وعدم وجود النص `openrouter` بأي حال أحرف)، `test_default_settings_have_no_llm_keys`، `test_init_default_settings_idempotent` ، `test_requirements_have_no_cloud_llm_packages`، `test_frontend_has_no_openrouter` (على `frontend/`).
- [ ] **Step 2: شغّل** `pytest tests/test_config_cleanup.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ التنظيف** وتأكد أن `python -c "import backend.main"` يعمل بعد الحذف.
- [ ] **Step 4: شغّل** `pytest -v` — المتوقع: نجاح الكل.
- [ ] **Step 5: Commit** — `chore: remove OpenRouter and cloud LLM leftovers`

### Task 10: التشغيل على ويندوز والتوثيق

**Files:**
- Delete: `render.yaml`
- Create: `scripts/windows/run.ps1` ، `scripts/windows/install-service.ps1` ، `scripts/windows/uninstall-service.ps1`
- Modify: `.env.example` (المتغيرات الجديدة بقيم توضيحية وتعليق أن `HERMES_BASE_URL` يُملأ بعنوان Hermes الفعلي على جهازك؛ حذف إشارات Render و`PORT`)
- Modify: `README.md` (قسم التشغيل المحلي على ويندوز، الربط مع Hermes، أوامر تيليجرام، واجهة التحكم وعناوينها)
- Modify: `backend/config.py` (حذف تحذير Render عن SQLite في الإنتاج)
- Test: `tests/test_docs_and_scripts.py`

- [ ] **Step 1: اكتب الاختبارات الفاشلة.** `test_render_yaml_removed`، `test_env_example_lists_new_vars` (الخمسة)، `test_no_render_mentions_in_readme`، `test_scripts_exist` (الثلاثة)، `test_install_script_registers_restart_on_failure` (النص يحوي `Register-ScheduledTask` و`RestartCount` و`AtStartup`).
- [ ] **Step 2: شغّل** `pytest tests/test_docs_and_scripts.py -v` — المتوقع: فشل.
- [ ] **Step 3: نفّذ.** `run.ps1` ينشئ/يفعّل venv ويثبّت المتطلبات ثم يشغّل `uvicorn backend.main:app --host 127.0.0.1 --port 8000` ويقرأ `.env`. `install-service.ps1` (يتطلب صلاحية مدير) يسجّل مهمة مجدولة عند الإقلاع تشغّل `run.ps1` مع إعادة التشغيل عند الفشل كل دقيقة حتى 999 مرة وتعمل ولو لم يسجّل المستخدم الدخول.
- [ ] **Step 4: شغّل** `pytest -v` — المتوقع: نجاح الكل. ثم تحقّق من بناء الجملة: `pwsh -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw scripts/windows/run.ps1)) | Out-Null"` لكل سكربت إن توفر `pwsh` في البيئة؛ وإلا يُسجَّل في الـ PR أن السكربتات لم تُجرَّب على ويندوز، ويُجريها المالك يدوياً.
- [ ] **Step 5: Commit** — `feat(deploy): Windows service scripts, drop Render, docs`

---

## مراجعة ذاتية للخطة

- **تغطية المواصفة:** العميل (T1)، الإيقاف والحواجز والحد اليومي (T2)، أوامر تيليجرام (T3)، التنبيه مرة واحدة والاستئناف (T4)، ai_service وحذف القوالب الثابتة (T5)، اختيار العروض والتصنيفات وحماية `publish_product` (T6)، واجهة التحكم (T7)، ربط المراقب والمستمع (T8)، حذف OpenRouter (T9)، ويندوز والتوثيق (T10).
- **قراران من المصمم** (لم يرد صراحة في المواصفة، للمراجعة): (1) Hermes يستطيع `pause` لكن لا يستطيع رفع الإيقاف اليدوي، لأن مفتاح القتل للمالك؛ (2) مراقب Hermes يعمل كل دقيقتين.
- **ما لا يمكن اختباره هنا:** الاتصال بـ Hermes الحقيقي، والبوت الحقيقي، وسكربتات ويندوز. اختبارات الخطة تعتمد على الحقن؛ التحقق النهائي يجري على جهازك بعد التنفيذ.
