# 🕷️ دليل Spider الكامل — ربط أزرار واجهة التحكم

## نظرة عامة

هذا الدليل يشرح كل زر في `spider_gait_controller.html`، ما يفعله بالضبط،
وكيف تُنفّذه في `web_controller.py` وتربطها مع الواجهة الأساسية `index.html`.

---

## 1️⃣ الأزرار الجاهزة (موجودة بالفعل في web_controller.py)

هذه الأزرار تعمل فوراً بدون أي تعديل:

| الزر | الـ Endpoint | الوصف |
|------|-------------|-------|
| ⏹ إيقاف طارئ | `POST /api/calibrate` + `POST /api/gait/forward/stop` | يوقف الحركة ويرجع لوضعية الوقوف |
| ⬜ وضعية الوقوف | `POST /api/calibrate` | يرجّع كل المحركات للوضعية الافتراضية |
| 🔴 إطفاء المحركات | `POST /api/off` | يقطع PWM عن كل المحركات (PCA sleep) |
| IMU — تشغيل/إيقاف | `GET /api/imu/read` | يقرأ Roll / Pitch / Yaw من BNO085 |
| ▶ مشي أمام | `POST /api/gait/forward/start` | يشغّل Forward Tripod Gait |
| ⏹ وقف الحركة | `POST /api/gait/forward/stop` | يوقف الـ gait بشكل نظيف |

---

## 2️⃣ الأزرار الجديدة المطلوبة

### 2-A: أزرار الاتجاهات (D-Pad)

كل زر يرسل `POST /api/gait/forward/start` مع `type` مختلف.
عدّل `_run_forward_gait` لتقبل معامل `gait_type`:

```python
@app.route('/api/gait/forward/start', methods=['POST'])
def gait_forward_start():
    data      = request.json or {}
    gait_type = data.get('type', 'forward')   # ← جديد
    speed     = float(data.get('speed', 1.0))
    ...
    gait_thread = threading.Thread(
        target=_run_forward_gait,
        args=(params, speed, 0, gait_type),   # ← مرّر النوع
        daemon=True
    )
```

#### جدول أنواع الحركة وما يتغير فيها

| النوع `type` | الزر | الفكرة الحركية | ما يتغير في الكود |
|-------------|------|---------------|-----------------|
| `forward`   | ▲ | Coxa يتقدم للأمام | كما هو في `spider_forward_command_v2.md` |
| `backward`  | ▼ | عكس `forward` تماماً | اعكس `direction` في `get_leg_angles()` |
| `turn_left` | ↺ | GROUP_A يتقدم، GROUP_B يتقدم عكس | يمين يتقدم، يسار يتأخر |
| `turn_right`| ↻ | عكس `turn_left` | يسار يتقدم، يمين يتأخر |
| `shift_left`| ◀ | كل الأرجل تتحرك للجانب الأيسر | تغيير Coxa للزاوية الجانبية بدل الأمامية |
| `shift_right`| ▶ | كل الأرجل تتحرك للجانب الأيمن | عكس `shift_left` |
| `strafe_left` | ⬅ | زحف جانبي يسار (مشية سرطان) | Coxa على 60° بدل 90° للجانب الأيسر |
| `strafe_right`| ➡ | زحف جانبي يمين | Coxa على 120° بدل 90° للجانب الأيمن |
| `climb`     | 🧗 | خطوات عالية وبطيئة للتضاريس | زود `femur_lift` بـ +15° وخفّف السرعة |
| `crab_walk` | 🦀 | مشية سرطان كاملة | GROUP_A و GROUP_B يتحركان بشكل جانبي |
| `creep`     | 🐛 | زحف بطيء للاستقرار القصوى | خفّف السرعة × 0.3 وزود smooth_steps |

#### تنفيذ `backward` (مثال):

```python
def get_leg_angles(leg_name, frame, params, gait_type='forward'):
    ...
    # عكس الاتجاه للخلف
    if gait_type == 'backward':
        direction = -direction

    # للدوران: الجانب اليمين والأيسر يتحركان بعكس بعض
    if gait_type == 'turn_left':
        if side == 'L':
            direction = -direction  # اليسار يتأخر = يتحرك للأمام = دوران يسار
    if gait_type == 'turn_right':
        if side == 'R':
            direction = -direction
    ...
```

---

### 2-B: حركة دوران مكاني 90°

```python
@app.route('/api/gait/once', methods=['POST'])
def gait_once():
    """ينفذ N دورة من نوع معين ثم يوقف."""
    data      = request.json or {}
    gait_type = data.get('type', 'rotate_cw')
    speed     = float(data.get('speed', 0.8))
    cycles    = int(data.get('cycles', 4))   # 4 دورات ≈ 90°

    params = _load_gait_params('forward_tripod')
    if not params:
        return jsonify({'ok': False, 'error': 'params not found'})

    global gait_running, gait_thread
    if gait_running:
        return jsonify({'ok': False, 'error': 'already running'})

    gait_running = True
    gait_thread  = threading.Thread(
        target=_run_forward_gait,
        args=(params, speed, cycles, gait_type),
        daemon=True
    )
    gait_thread.start()
    return jsonify({'ok': True})
```

---

### 2-C: حركات الجسم

```python
@app.route('/api/body/move', methods=['POST'])
def body_move():
    """
    يحرّك الجسم بتغيير Femur أو Coxa لكل الأرجل معاً.
    لا يتضمن locomotion — الأرجل تبقى على الأرض.
    """
    data  = request.json or {}
    move  = data.get('move', 'stand')
    speed = float(data.get('speed', 1.0))

    # الوضعيات الأساسية (من leg defaults)
    base = {
        'R0':90,'R1':67,'R2':91,
        'R3':92,'R4':57,'R5':92,
        'R6':93,'R7':65,'R8':94,
        'L0':90,'L1':54,'L2':77,
        'L3':85,'L4':74,'L5':94,
        'L6':94,'L7':64,'L8':89
    }

    BODY_DELTA = 12  # درجات التعديل

    moves = {
        # ارتفاع: كل Femur ترتفع قليلاً (قيمة أكبر = أعلى)
        'body_up':      {k: base[k] + BODY_DELTA if k[1]=='1' or k[1]=='4' or k[1]=='7' else base[k]
                         for k in base},
        # انخفاض: كل Femur تنزل
        'body_down':    {k: base[k] - BODY_DELTA if k[1]=='1' or k[1]=='4' or k[1]=='7' else base[k]
                         for k in base},
        # ميل أمام: الأرجل الأمامية ترتفع، الخلفية تنزل
        'lean_forward': {**base,
                         'R1': base['R1'] + BODY_DELTA, 'L1': base['L1'] + BODY_DELTA,
                         'R7': base['R7'] - BODY_DELTA, 'L7': base['L7'] - BODY_DELTA},
        # ميل خلف
        'lean_back':    {**base,
                         'R1': base['R1'] - BODY_DELTA, 'L1': base['L1'] - BODY_DELTA,
                         'R7': base['R7'] + BODY_DELTA, 'L7': base['L7'] + BODY_DELTA},
        # ميل يسار: اليسار يرتفع، اليمين ينزل
        'lean_left':    {**base,
                         'L1': base['L1'] + BODY_DELTA, 'L4': base['L4'] + BODY_DELTA, 'L7': base['L7'] + BODY_DELTA,
                         'R1': base['R1'] - BODY_DELTA, 'R4': base['R4'] - BODY_DELTA, 'R7': base['R7'] - BODY_DELTA},
        # ميل يمين
        'lean_right':   {**base,
                         'R1': base['R1'] + BODY_DELTA, 'R4': base['R4'] + BODY_DELTA, 'R7': base['R7'] + BODY_DELTA,
                         'L1': base['L1'] - BODY_DELTA, 'L4': base['L4'] - BODY_DELTA, 'L7': base['L7'] - BODY_DELTA},
        # لي يسار: الأرجل الأمامية تدور يمين، الخلفية يسار (Coxa)
        'twist_left':   {**base,
                         'R0': base['R0'] + BODY_DELTA, 'L0': base['L0'] - BODY_DELTA,
                         'R6': base['R6'] - BODY_DELTA, 'L6': base['L6'] + BODY_DELTA},
        # لي يمين
        'twist_right':  {**base,
                         'R0': base['R0'] - BODY_DELTA, 'L0': base['L0'] + BODY_DELTA,
                         'R6': base['R6'] + BODY_DELTA, 'L6': base['L6'] - BODY_DELTA},
    }

    if move not in moves:
        return jsonify({'ok': False, 'error': f'unknown move: {move}'})

    targets = {k: max(45, min(135, v)) for k, v in moves[move].items()}
    steps   = max(4, int(10 / speed))
    delay   = max(0.01, 0.03 / speed)
    smooth_move_leg(list(targets.keys()), targets, steps=steps, delay=delay)

    return jsonify({'ok': True, 'move': move})
```

---

### 2-D: الحركات الاستعراضية

كل حركة = سلسلة من `smooth_move_leg()` تُنفَّذ في thread منفصل.

```python
@app.route('/api/special/<move_name>', methods=['POST'])
def special_move(move_name):
    data  = request.json or {}
    speed = float(data.get('speed', 1.0))

    specials = {
        'wave':       _move_wave,
        'dance':      _move_dance,
        'shake':      _move_shake,
        'salute':     _move_salute,
        'roar':       _move_roar,
        'spin':       _move_spin,
        'bow':        _move_bow,
        'stretch':    _move_stretch,
        'idle_sway':  _move_idle_sway,
        'wake_up':    _move_wake_up,
        'sleep_pose': _move_sleep_pose,
    }

    fn = specials.get(move_name)
    if not fn:
        return jsonify({'ok': False, 'error': f'unknown special: {move_name}'})

    t = threading.Thread(target=fn, args=(speed,), daemon=True)
    t.start()
    return jsonify({'ok': True, 'move': move_name})
```

#### تنفيذ كل حركة استعراضية:

```python
# ── وضعية الوقوف الأساسية (مرجع) ────────────────
_STAND = {
    'R0':90,'R1':67,'R2':91,
    'R3':92,'R4':57,'R5':92,
    'R6':93,'R7':65,'R8':94,
    'L0':90,'L1':54,'L2':77,
    'L3':85,'L4':74,'L5':94,
    'L6':94,'L7':64,'L8':89
}

def _move_wave(speed=1.0):
    """👋 تلويح — RF ترتفع وتتأرجح يمين يسار 3 مرات."""
    s  = max(4, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    # ارفع RF
    smooth_move_leg(['R0','R1','R2'], {'R0':90,'R1':119,'R2':101}, steps=s, delay=dl)
    for _ in range(3):
        smooth_move_leg(['R0'], {'R0':115}, steps=s, delay=dl)
        smooth_move_leg(['R0'], {'R0':65},  steps=s, delay=dl)
    # أنزل RF
    smooth_move_leg(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_dance(speed=1.0):
    """💃 رقص — تأرجح الجسم يمين/يسار مع رفع أرجل متناوبة."""
    s  = max(3, int(6 / speed))
    dl = max(0.01, 0.02 / speed)
    for _ in range(2):
        # ميل يمين + رفع RF
        smooth_move_leg(
            ['R1','R4','R7','L1','L4','L7','R0','R1'],
            {'R1':77,'R4':67,'R7':75,'L1':44,'L4':64,'L7':54,'R0':110},
            steps=s, delay=dl
        )
        time.sleep(0.1)
        # ميل يسار + رفع LF
        smooth_move_leg(
            ['R1','R4','R7','L1','L4','L7','L0','L1'],
            {'R1':57,'R4':47,'R7':55,'L1':64,'L4':84,'L7':74,'L0':70},
            steps=s, delay=dl
        )
        time.sleep(0.1)
    smooth_move_leg(list(_STAND.keys()), _STAND, steps=8, delay=0.03)


def _move_shake(speed=1.0):
    """🤝 مصافحة — RF تمتد للأمام وتهتز."""
    s  = max(4, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    smooth_move_leg(['R0','R1','R2'], {'R0':125,'R1':90,'R2':70}, steps=s, delay=dl)
    for _ in range(3):
        smooth_move_leg(['R1'], {'R1':100}, steps=3, delay=0.02)
        smooth_move_leg(['R1'], {'R1':80},  steps=3, delay=0.02)
    smooth_move_leg(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_salute(speed=1.0):
    """🫡 تحية — RF ترتفع وتلمس الجانب الأيمن للجسم."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    smooth_move_leg(['R0','R1','R2'], {'R0':60,'R1':125,'R2':130}, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    smooth_move_leg(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_roar(speed=1.0):
    """💥 تهديد — كل الأرجل الأمامية ترتفع والجسم ينخفض."""
    s  = max(4, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    # الجسم ينخفض
    low = {k: v-10 if k in ('R1','R4','R7','L1','L4','L7') else v for k,v in _STAND.items()}
    smooth_move_leg(list(low.keys()), low, steps=s, delay=dl)
    # الأرجل الأمامية ترتفع
    smooth_move_leg(['R0','R1','L0','L1'], {'R0':130,'R1':119,'L0':50,'L1':127}, steps=s, delay=dl)
    time.sleep(0.4 / speed)
    smooth_move_leg(list(_STAND.keys()), _STAND, steps=8, delay=0.03)


def _move_spin(speed=1.0):
    """🌀 دوران كامل 360° — 8 دورات turn_right."""
    params = _load_gait_params('forward_tripod')
    if params:
        global gait_running
        gait_running = True
        _run_forward_gait(params, speed * 1.2, 8, 'turn_right')


def _move_bow(speed=1.0):
    """🙇 انحناء — الأرجل الأمامية ترفع الجسم، الخلفية تنخفض."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    bow = {**_STAND,
           'R1': _STAND['R1'] + 20, 'L1': _STAND['L1'] + 20,   # أمام ترتفع
           'R7': _STAND['R7'] - 15, 'L7': _STAND['L7'] - 15}   # خلف تنزل
    smooth_move_leg(list(bow.keys()), bow, steps=s, delay=dl)
    time.sleep(0.6 / speed)
    smooth_move_leg(list(_STAND.keys()), _STAND, steps=s, delay=dl)


def _move_stretch(speed=1.0):
    """🦾 تمدد — كل الأرجل تمتد للخارج إلى أقصى مدى."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    # Coxa إلى 45 (خلف) لليمين، 135 (خلف) لليسار = كل الأرجل للخارج
    stretch = {**_STAND,
               'R0':70,'R3':70,'R6':70,
               'L0':110,'L3':110,'L6':110,
               'R1':_STAND['R1']-10,'R4':_STAND['R4']-10,'R7':_STAND['R7']-10,
               'L1':_STAND['L1']-10,'L4':_STAND['L4']-10,'L7':_STAND['L7']-10}
    smooth_move_leg(list(stretch.keys()), stretch, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    smooth_move_leg(list(_STAND.keys()), _STAND, steps=s, delay=dl)


def _move_idle_sway(speed=1.0):
    """🌊 تأرجح خفيف — حركة هادئة للاسترخاء (5 دورات)."""
    s  = max(6, int(12 / speed))
    dl = max(0.02, 0.04 / speed)
    for _ in range(5):
        sway_r = {**_STAND,
                  'R1':_STAND['R1']+8,'R4':_STAND['R4']+8,'R7':_STAND['R7']+8,
                  'L1':_STAND['L1']-8,'L4':_STAND['L4']-8,'L7':_STAND['L7']-8}
        sway_l = {**_STAND,
                  'L1':_STAND['L1']+8,'L4':_STAND['L4']+8,'L7':_STAND['L7']+8,
                  'R1':_STAND['R1']-8,'R4':_STAND['R4']-8,'R7':_STAND['R7']-8}
        smooth_move_leg(list(sway_r.keys()), sway_r, steps=s, delay=dl)
        smooth_move_leg(list(sway_l.keys()), sway_l, steps=s, delay=dl)
    smooth_move_leg(list(_STAND.keys()), _STAND, steps=s, delay=dl)


def _move_wake_up(speed=1.0):
    """✅ إيقاظ — ينزل ببطء من وضعية نوم لوضعية وقوف."""
    s  = max(8, int(15 / speed))
    dl = max(0.02, 0.04 / speed)
    sleep_pos = {k: 45 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    smooth_move_leg(list(_STAND.keys()), sleep_pos, steps=2, delay=0.01)  # إلى النوم أولاً
    smooth_move_leg(list(_STAND.keys()), _STAND,    steps=s, delay=dl)    # ثم إيقاظ بطيء


def _move_sleep_pose(speed=1.0):
    """💤 وضعية نوم — كل الأرجل تنزل إلى أدنى نقطة."""
    s  = max(8, int(15 / speed))
    dl = max(0.02, 0.04 / speed)
    sleep_pos = {**_STAND}
    for k in list(sleep_pos.keys()):
        if k[1] in ('1','4','7'):     # Femur keys
            sleep_pos[k] = 45
        elif k[1] in ('2','5','8'):   # Tibia keys
            sleep_pos[k] = 45
    smooth_move_leg(list(sleep_pos.keys()), sleep_pos, steps=s, delay=dl)
```

---

## 3️⃣ دمج الواجهة مع index.html الأساسي

### الطريقة الموصى بها: Tab إضافي

أضف في أعلى `index.html` زر للانتقال:

```html
<!-- في شريط التنقل الأعلى، بعد العنوان -->
<a href="/gait" target="_blank"
   style="background:#0a5c2e; color:#22c55e; padding:8px 16px;
          border-radius:8px; border:1px solid rgba(34,197,94,0.3);
          text-decoration:none; font-size:13px; font-weight:700;">
  🕹️ لوحة الحركة
</a>
```

### إضافة Route في web_controller.py:

```python
@app.route('/gait')
def gait_controller():
    return render_template('spider_gait_controller.html')
```

ضع `spider_gait_controller.html` في مجلد `templates/`.

### أو تضمينها مباشرة في index.html (Tab):

```html
<!-- أضف Tab في أعلى الصفحة -->
<div style="display:flex; gap:4px; margin-bottom:16px;">
  <button onclick="showTab('control')" id="tab-control" class="tab-btn active">⚙️ تحكم</button>
  <button onclick="showTab('gait')"    id="tab-gait"    class="tab-btn">🕹️ حركة</button>
</div>
<div id="panel-control"><!-- محتوى index الحالي --></div>
<div id="panel-gait" style="display:none">
  <!-- iframe للواجهة الجديدة -->
  <iframe src="/gait" style="width:100%; height:80vh; border:none; border-radius:12px;"></iframe>
</div>

<script>
function showTab(name) {
  document.getElementById('panel-control').style.display = name==='control' ? '' : 'none';
  document.getElementById('panel-gait').style.display    = name==='gait'    ? '' : 'none';
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
}
</script>
```

---

## 4️⃣ تحسين العرض على الهاتف المحمول

الواجهة الحالية responsive لكن هذه التحسينات تجعلها أفضل على الهاتف:

### أ) أضف هذا CSS في `spider_gait_controller.html` بعد الـ styles الموجودة:

```css
/* ── تحسينات الهاتف ── */
@media (max-width: 480px) {

  /* D-Pad أكبر على الهاتف */
  .dpad { grid-template-columns: repeat(3, 70px); grid-template-rows: repeat(3, 70px); }
  .dpad .btn { width: 70px; height: 70px; font-size: 24px; }

  /* الأزرار تملأ العرض الكامل */
  .special-grid { grid-template-columns: repeat(2, 1fr); }
  .body-grid    { grid-template-columns: repeat(2, 1fr); }
  .rotate-grid  { grid-template-columns: repeat(2, 1fr); }

  /* Header أصغر */
  header { padding: 10px 14px; }
  .logo  { font-size: 16px; }

  /* الـ main padding أقل */
  main  { padding: 10px; gap: 10px; }
  .card { padding: 14px; }

  /* زر الإيقاف الطارئ أكبر وأكثر وضوحاً */
  .btn-stop-big { padding: 18px; font-size: 18px; }

  /* IMU أرقام أكبر */
  .imu-val .num { font-size: 24px; }

  /* إخفاء النصوص الطويلة */
  .status-bar span:not(#hw-label) { display: none; }
}

/* منع تكبير النص عند الضغط على input في iOS */
input[type=range] { font-size: 16px; }

/* تحسين اللمس — مساحة أكبر للأصابع */
.btn { min-height: 44px; min-width: 44px; }
```

### ب) أضف meta tags في `<head>`:

```html
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0d0d0f">
```

### ج) اجعل الـ D-Pad يعمل أحسن باللمس:

```javascript
// أضف في قسم الـ Script
// منع تأخير اللمس في iOS
document.querySelectorAll('.btn').forEach(btn => {
  btn.addEventListener('touchstart', () => {}, { passive: true });
});

// منع scroll أثناء الضغط على الأزرار
document.addEventListener('touchmove', e => {
  if (e.target.closest('.dpad')) e.preventDefault();
}, { passive: false });
```

---

## 5️⃣ ترتيب التنفيذ الموصى به

```
الأولوية 1 (تشتغل فوراً):
✅ أضف Route /gait → render_template('spider_gait_controller.html')
✅ ضع الملف في templates/
✅ اختبر الاتصال — الأزرار الموجودة يجب تشتغل

الأولوية 2 (الحركات الأساسية):
□ أضف معامل type لـ /api/gait/forward/start
□ عدّل get_leg_angles() لتقبل gait_type
□ نفّذ: backward, turn_left, turn_right

الأولوية 3 (حركات الجسم):
□ أضف /api/body/move endpoint
□ نفّذ: body_up, body_down, lean_*, twist_*

الأولوية 4 (استعراضية):
□ أضف /api/special/<move_name> endpoint
□ نفّذ الدوال بالترتيب: wake_up → sleep_pose → wave → bow → rest

الأولوية 5 (تجميل):
□ أضف CSS تحسينات الهاتف
□ أضف meta tags
□ اختبر على هاتف حقيقي
```

---

## 6️⃣ اختبار سريع بعد كل مرحلة

```bash
# اختبر backward
curl -X POST http://localhost:5000/api/gait/forward/start \
  -H "Content-Type: application/json" \
  -d '{"type":"backward","speed":0.5}'

# اختبر دوران يسار
curl -X POST http://localhost:5000/api/gait/forward/start \
  -H "Content-Type: application/json" \
  -d '{"type":"turn_left","speed":0.7}'

# اختبر ميل الجسم
curl -X POST http://localhost:5000/api/body/move \
  -H "Content-Type: application/json" \
  -d '{"move":"lean_forward"}'

# اختبر تلويح
curl -X POST http://localhost:5000/api/special/wave \
  -H "Content-Type: application/json" \
  -d '{"speed":1.0}'

# وقف كل شي
curl -X POST http://localhost:5000/api/gait/forward/stop
```

---

## ملخص الـ Endpoints الجديدة

| Endpoint | Method | الوصف |
|----------|--------|-------|
| `/api/gait/forward/start` | POST | تقبل الآن `type` لكل الاتجاهات |
| `/api/gait/once` | POST | دورات محدودة (للدوران 90°) |
| `/api/body/move` | POST | حركات الجسم بدون locomotion |
| `/api/special/<name>` | POST | كل الحركات الاستعراضية |
| `/gait` | GET | يعرض واجهة التحكم الكاملة |

