# خطة 05 — إعادة تصميم الواجهة الأمامية وتبسيط الأزرار

> الأولوية: 🟠 عالية. تعتمد على الخطط 01–04 للـ API الجديد، لكن جزء التدقيق ينفع فوراً.

---

## ملخص تنفيذي

الواجهة الحالية (`spider_gait_controller.html`) فيها ~70 زراً في صفحة واحدة طويلة، بعضها يعمل وبعضها لا، بلا تمييز بصري. الحل:
1. **تدقيق كل زر** (يعمل/معطّل/خطر) — جدول أدناه.
2. **واجهة بتبويبات**: «تحكّم» (بسيط، يومي) / «حركات» / «حساسات وخريطة» / «إعدادات متقدّمة».
3. **عصا تحكّم واحدة (joystick)** تنتج `(vx,vy,ω)` مباشرةً — تستبدل عشرات أزرار الاتجاهات.
4. **حالة حيّة** لكل ميزة (متصل/معطّل) فلا يضغط المستخدم زراً ميتاً.

---

## تدقيق الأزرار الحالية

> ✅ يعمل · ⚠️ يعمل بمشاكل · ❌ معطّل/خطر · 🔁 يُستبدل

| الزر | المسار | الحالة | ملاحظة |
|------|--------|--------|--------|
| إيقاف طارئ | `/api/calibrate` | ❌ **خطر** | يُطلق حركة 18 محرك! → الخطة 01 يصلحه لقطع فيزيائي |
| ▲▼ أمام/خلف | `/api/gait/forward/start` | ✅ | → joystick |
| ↺↻ دوران | forward/start turn_* | ✅ | → joystick |
| انزياح/زحف يسار/يمين | forward/start (lateral) | ✅ بعد الإصلاح | عبر `_run_lateral_gait` |
| مشية سرطان | crab_walk | ✅ | |
| تسلق/زحف/تسلل/عسكري/انزلاق | forward/start | ✅ | أنماط — تصير قائمة منسدلة |
| دوران 90/180° | `/api/gait/once` | ✅ | |
| حركات الجسم (9) | `/api/body/move` | ⚠️ | تعمل لكن بلا حدّ سرعة آمن (الخطة 01) |
| حركات استعراضية (23) | `/api/special/*` | ⚠️ | تعمل لكن **لا تفحص انشغال المشي** → تضارب (الخطة 01) |
| إطفاء المحركات | `/api/off` | ⚠️ | خطأ الطرف الأيسر (#4) → الخطة 01 |
| توازن تشغيل/إيقاف | `/api/balance/*` | ✅ | يكتب مباشرة → يُعاد ربطه (الخطة 03) |
| ضبط PID ±| `/api/balance/tune` | ✅ | |
| Smooth Ripple | `/api/gait/smooth-ripple/*` | ⚠️ | منطق منفصل مكرّر → يُدمج بالمحرك الموحّد (الخطة 03) |
| نمط النعومة | `/api/easing/*` | ✅ | ينتقل لإعدادات متقدّمة |
| IMU تشغيل/تصفير | `/api/imu/*` | ✅ | |
| محاكاة 3D | iframe `/sim` | ✅ | يبقى |

**الخلاصة:** زر الطوارئ خطر، 23 حركة استعراضية + 9 حركات جسم تفتقد التحكيم، Smooth Ripple مكرّر. كلها تُصلَح في الخطط 01/03.

---

## بنية الواجهة الجديدة — `templates/dashboard.html`

تبويبات علوية:

```
┌──────────────────────────────────────────────┐
│ 🕷️ Spider   [🟢 متصل] [🧭IMU] [📍GPS] [⛔طوارئ]│  ← شريط ثابت دائماً
├──────────────────────────────────────────────┤
│ [تحكّم] [حركات] [حساسات وخريطة] [إعدادات]      │  ← تبويبات
├──────────────────────────────────────────────┤
│  تبويب «تحكّم»:                                │
│   ┌─────────────┐   السرعة: [▁▂▃▄▅] 1.0×       │
│   │   عصا       │   النمط:  [Tripod ▾]          │
│   │  التحكّم    │   [⏹ قف]                      │
│   │   (vx,vy)   │   الدوران: [↺]━━●━━[↻]        │
│   └─────────────┘                              │
│   ارتفاع الجسم: [▁▂▃▄▅]  ميل: [pad]            │
├──────────────────────────────────────────────┤
│  زر طوارئ كبير أحمر ثابت أسفل الشاشة            │
└──────────────────────────────────────────────┘
```

### عصا التحكّم (الجوهر)
لمسة/سحب داخل دائرة → ينتج `(vx, vy)` مطبّع، ودوّار منفصل لـ `ω`. يرسل `/api/move` كل ~120ms أثناء السحب، و`vx=vy=0` عند الإفلات.

```html
<canvas id="joy" width="220" height="220"></canvas>
<input id="omega" type="range" min="-1" max="1" step="0.05" value="0">
<script>
const joy = document.getElementById('joy'), jx = joy.getContext('2d');
let active=false, vx=0, vy=0, sendTimer=null;
const R=100, CX=110, CY=110;
function draw(px,py){
  jx.clearRect(0,0,220,220);
  jx.strokeStyle='#2a2a4a'; jx.beginPath(); jx.arc(CX,CY,R,0,7); jx.stroke();
  jx.fillStyle='#58a6ff'; jx.beginPath(); jx.arc(CX+px,CY+py,22,0,7); jx.fill();
}
draw(0,0);
function setFromEvent(e){
  const r=joy.getBoundingClientRect();
  const t=e.touches?e.touches[0]:e;
  let dx=t.clientX-r.left-CX, dy=t.clientY-r.top-CY;
  const d=Math.hypot(dx,dy); if(d>R){dx*=R/d; dy*=R/d;}
  draw(dx,dy);
  vy = -(dx/R);      // يمين موجب dx → vy سالب (يسار موجب)
  vx = -(dy/R);      // أعلى dy سالب → vx موجب (أمام)
}
function startSend(){ if(!sendTimer) sendTimer=setInterval(sendMove,120); }
function sendMove(){
  const speed=getSpeed();
  const om=parseFloat(document.getElementById('omega').value);
  fetch('/api/move',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({vx:vx*speed, vy:vy*speed, omega:om*speed,
                         gait:document.getElementById('gaitSel').value})});
}
function release(){ active=false; vx=vy=0; draw(0,0);
  clearInterval(sendTimer); sendTimer=null;
  fetch('/api/move',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({vx:0,vy:0,omega:0})}); }
['mousedown','touchstart'].forEach(ev=>joy.addEventListener(ev,e=>{active=true;setFromEvent(e);startSend();e.preventDefault();}));
['mousemove','touchmove'].forEach(ev=>joy.addEventListener(ev,e=>{if(active){setFromEvent(e);e.preventDefault();}}));
['mouseup','touchend','mouseleave'].forEach(ev=>joy.addEventListener(ev,release));
</script>
```

### مسار `/api/move` الموحّد (الخطة 03)
```python
@app.route("/api/move", methods=["POST"])
def api_move():
    d = request.json or {}
    vx, vy = float(d.get("vx",0)), float(d.get("vy",0))
    om     = float(d.get("omega",0))
    name   = d.get("gait","tripod")
    if abs(vx)<1e-3 and abs(vy)<1e-3 and abs(om)<1e-3:
        gait.stop(); return jsonify({"ok": True, "stopped": True})
    sp = json.load(open("config/motion.json"))["walk"]["speed_mm_per_cycle"]
    gait.start(name, vx*sp, vy*sp, om)   # start يحدّث cmd لو شغّال
    return jsonify({"ok": True, "status": gait.status()})
```

---

## تبويب «حركات» (الاستعراضية + الجسم)

تبقى الأزرار لكن:
- تُعطَّل بصرياً (رمادية + معطّلة) إذا `gait.status().state != "idle"` — تُحدَّث عبر polling.
- كل زر يستدعي `/api/special/*` الذي صار يفحص التحكيم (الخطة 01) ويعيد خطأ واضحاً لو مشغول.
- تُجمَّع في فئات قابلة للطي: «أساسية / استعراضية / تمارين / تعبيرية».

```javascript
async function refreshBusy(){
  const s = await (await fetch('/api/gait/forward/status')).json();
  const busy = s.state && s.state !== 'idle';
  document.querySelectorAll('.move-btn').forEach(b=>{
    b.disabled = busy; b.style.opacity = busy ? .4 : 1;
  });
}
setInterval(refreshBusy, 700);
```

---

## تبويب «إعدادات متقدّمة»

محرّر بارامترات عام يقرأ/يكتب عبر `/api/config/<name>` (الخطة 04):
- قائمة الملفات: motion / gaits / balance / robot_geometry / servo_limits.
- لكل بارامتر: عنوان + شرح (من جدول الخطة 04) + مزلاج/حقل + زر «حفظ» + «استعادة».
- قسم **معايرة السيرفو**: 18 محرك، لكل واحد أزرار nudge ±، إدخال حدود آمنة، حفظ.
- قسم **تشخيص**: تيار/جهد (إن وُجد INA219)، حرارة، آخر أخطاء.

```html
<select id="cfgFile" onchange="loadCfg()">
  <option>motion</option><option>gaits</option><option>balance</option>
  <option>robot_geometry</option><option>servo_limits</option>
</select>
<div id="cfgEditor"></div>
<button onclick="saveCfg()">💾 حفظ</button>
<button onclick="restoreCfg()">↩️ استعادة النسخة الاحتياطية</button>
<script>
let curCfg={}, curName='motion';
async function loadCfg(){
  curName=document.getElementById('cfgFile').value;
  curCfg=await (await fetch('/api/config/'+curName)).json();
  document.getElementById('cfgEditor').innerHTML =
    '<textarea id="cfgText" style="width:100%;height:320px;font-family:monospace">'
    + JSON.stringify(curCfg,null,2) + '</textarea>';
}
async function saveCfg(){
  const body=JSON.parse(document.getElementById('cfgText').value);
  await fetch('/api/config/'+curName,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  alert('حُفظ. قد تحتاج إعادة تحميل الوحدة.');
}
async function restoreCfg(){ await fetch('/api/config/'+curName+'/restore',{method:'POST'}); loadCfg(); }
loadCfg();
</script>
```

(لاحقاً يُستبدل الـ textarea بحقول مولّدة من حقل `_doc` — لكن الـ textarea يعمل فوراً وآمن.)

---

## تبويب «حساسات وخريطة»

حاوية للخطط 06–09: بثّ الكاميرات + الخريطة + لوحة الليدار + قراءات الرطوبة. (تفاصيلها في تلك الخطط.)

---

## خطوات التطبيق

1. (فوري، بدون انتظار باقي الخطط) **أصلِح زر الطوارئ في الواجهة الحالية** ليستدعي `/api/estop` (الخطة 01) — أهم إصلاح مرئي.
2. ابنِ `dashboard.html` بالتبويبات + عصا التحكّم → `/api/move`.
3. انقل الحركات الاستعراضية للتبويب مع تعطيل-عند-الانشغال.
4. ابنِ تبويب الإعدادات (محرّر JSON + معايرة).
5. اجعل `/` يفتح `dashboard.html` الجديد، وأبقِ القديم على `/legacy` احتياطاً.

---

## معايير القبول

- ✅ زر الطوارئ يقطع التغذية (لا يحرّك).
- ✅ عصا تحكّم واحدة تنتج كل اتجاهات الحركة (vx,vy,ω).
- ✅ لا يوجد زر «ميت» بلا مؤشّر حالة.
- ✅ الأزرار تتعطّل بصرياً أثناء انشغال الحركة.
- ✅ كل بارامتر قابل للضبط من تبويب الإعدادات.
- ✅ واجهة منظّمة بتبويبات لا صفحة واحدة طويلة.
```
