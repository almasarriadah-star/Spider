# spider/safety.py
"""المالك الوحيد للحركة: إدارة الطلبات والتحكيم بين الخيوط وتفادي تيار الذروة."""
import threading
import time
from spider import hardware
from spider.config import servo_limit

# ترتيب القنوات
ALL_KEYS = [f"{s}{c}" for s in ("R", "L") for c in range(9)]

# قيم التوازن الافتراضية للتهيئة لضمان عدم القفز المفاجئ
DEFAULT_RIGHT = {
    0: 90, 1: 67, 2: 91,
    3: 92, 4: 57, 5: 92,
    6: 93, 7: 65, 8: 94
}

DEFAULT_LEFT = {
    0: 90, 1: 54, 2: 77,
    3: 85, 4: 74, 5: 94,
    6: 94, 7: 64, 8: 89
}

# الحالة الحالية المعروفة (مصدر الحقيقة الوحيد للمشروع)
current = {}
for ch in range(9):
    current[f"R{ch}"] = DEFAULT_RIGHT[ch]
    current[f"L{ch}"] = DEFAULT_LEFT[ch]


class MotionArbiter:
    """المالك الوحيد لكتابة المحركات. حلقة بتردد ثابت تقرّب الوضع الحالي نحو الهدف
    بسرعة زاوية محدودة، وتُسلسِل الأحمال الكبيرة لتفادي تيار الذروة."""

    def __init__(self, rate_hz=50, max_deg_per_tick=4.0, max_simultaneous=18):
        self.dt = 1.0 / rate_hz
        self.max_deg_per_tick = max_deg_per_tick    # سقف سرعة الزاوية لكل محرك في التيك الواحد
        self.max_simultaneous = max_simultaneous    # كم محرك يتحرك فعلياً في tick واحد
        self._lock = threading.Lock()
        with self._lock:
            self.target = dict(current)
        self._estop = threading.Event()             # مضبوط = طوارئ
        self._owner = None                          # اسم المكون المالك للحركة حالياً
        self._stop = threading.Event()
        self._thread = None

    # ── واجهة الطلبات (يستدعيها أي مكوّن) ──
    def acquire(self, owner):
        """يطلب ملكية الحركة. يُرجع True لو نجح. يمنع تعدد الكتّاب."""
        with self._lock:
            if self._estop.is_set():
                return False
            # يسمح للمالك الحالي بالطلب مجدداً، أو إذا كانت الملكية شاغرة
            if self._owner is None or self._owner == owner:
                self._owner = owner
                return True
            return False

    def release(self, owner):
        """يحرر ملكية الحركة لو كان المالك هو نفسه طالب التحرير."""
        with self._lock:
            if self._owner == owner:
                self._owner = None
                return True
            return False

    def owner(self):
        with self._lock:
            return self._owner

    def set_target(self, owner, updates):
        """يضع أهدافاً جديدة (dict key->angle). تُطبَّق تدريجياً بسرعة محدودة."""
        with self._lock:
            if self._estop.is_set():
                return False
            if self._owner is not None and self._owner != owner:
                return False
            for k, a in updates.items():
                if k in self.target:
                    self.target[k] = servo_limit(k, a)
            return True

    # ── الإيقاف الطارئ ──
    def emergency_stop(self):
        """قطع فيزيائي فوري + تفريغ الأهداف. لا ينتظر I2C."""
        self._estop.set()
        hardware.power_cut()        # 1) اقطع التغذية فوراً (GPIO)
        hardware.pwm_release_all()  # 2) صفّر PWM لمنع سحب التيار
        with self._lock:
            self._owner = None
            # التزامن مع المكان الحالي لمنع الحركة المفاجئة عند العودة
            for k in ALL_KEYS:
                self.target[k] = current[k]

    def clear_estop(self):
        """يلغي حالة الطوارئ ويعيد التغذية (لا يحرّك — الهدف=الحالي)."""
        with self._lock:
            for k in ALL_KEYS:
                self.target[k] = current[k]
        self._estop.clear()
        hardware.power_enable()

    def in_estop(self):
        return self._estop.is_set()

    # ── الحلقة الرئيسية ──
    def start(self):
        hardware.power_enable()
        self._thread = threading.Thread(target=self._loop, name="MotionArbiterThread", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.monotonic()
            if not self._estop.is_set():
                self._tick()
            dt = self.dt - (time.monotonic() - t0)
            if dt > 0:
                time.sleep(dt)

    def _tick(self):
        """يقرّب current نحو target بخطوة محدودة، ويُسلسِل لو في محركات كثيرة بعيدة."""
        updates_to_write = {}
        with self._lock:
            # احسب الفروق
            diffs = []
            for k in ALL_KEYS:
                d = self.target[k] - current[k]
                if abs(d) > 0.5:
                    diffs.append((abs(d), k, d))
            
            if not diffs:
                return
                
            # رتّب حسب الأكبر فرقاً، وحرّك فقط أكبر N (تسلسل الحمل)
            diffs.sort(reverse=True)
            for _, k, d in diffs[: self.max_simultaneous]:
                step = max(-self.max_deg_per_tick, min(self.max_deg_per_tick, d))
                nv = servo_limit(k, current[k] + step)
                current[k] = nv
                updates_to_write[k] = nv
                
        if updates_to_write:
            hardware.write_batch_raw(updates_to_write)

    def shutdown(self):
        self._stop.set()


# نسخة وحيدة عامة في كامل التطبيق
arbiter = MotionArbiter()

def reload_motion():
    """يعيد تحميل إعدادات المحكّم من config/motion.json ويطبقها ديناميكياً."""
    import os
    import json
    from spider.config import CONFIG_DIR
    motion_file = os.path.join(CONFIG_DIR, "motion.json")
    try:
        if os.path.exists(motion_file):
            with open(motion_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            arb = cfg.get("arbiter", {})
            rate_hz = arb.get("rate_hz", 50)
            max_deg = arb.get("max_deg_per_tick", 4.0)
            max_sim = arb.get("max_simultaneous", 18)
            
            with arbiter._lock:
                arbiter.dt = 1.0 / rate_hz
                arbiter.max_deg_per_tick = max_deg
                arbiter.max_simultaneous = max_sim
            # print(f"Motion config reloaded in safety.py: rate={rate_hz}, max_deg={max_deg}, max_sim={max_sim}")
    except Exception as e:
        pass

# تحميل الإعدادات عند الإقلاع
reload_motion()

# ── مرجع _force_stop (يُضبط من web_controller عند الإيقاف الطارئ) ──
_force_stop_holder = [False]   # قائمة بعنصر واحد ليمكن تعديلها من الخارج

def _force_stop_ref():
    """يُرجع قيمة _force_stop الحالية. gaits/moves تستدعيها بدل استيراد متغير عالمي."""
    return _force_stop_holder[0]

def set_force_stop(val: bool):
    """يضبط قيمة _force_stop من web_controller."""
    _force_stop_holder[0] = val

