# spider/sensors/aux_servo.py
"""تحكّم بسيرفوات مساعدة (كاميرا SG90 / تربة DS3230) على قنوات PCA9685 الفاضية.

سيرفوان مساعدان ليسا أرجلاً فلا يمرّان بمولّد المشية — يُكتبان مباشرة عبر طبقة الهاردوير:
  • camera (SG90, R9): بانوراما 180°، زاوية وسط `home`.
  • soil   (DS3230, L9): إنزال/رفع حساس التربة بين `lower_angle` (غرز) و`raise_angle` (رفع).

كل الإعدادات (المفتاح/القطبية/الزوايا/الحدود) من config/sensors.json → aux_servo، وتُقرأ حيّاً
لتدعم config.reload_sensors(). القطبية (`invert`) تعكس الكتابة الفيزيائية مع إبقاء الزاوية
المنطقية المخزّنة كما هي.
"""
from spider import hardware
from spider import config

# تعيين اسم منطقي → مفتاح القناة (يُحدَّث من الإعدادات).
_KEYS = {}
# الحالة المخزّنة بالزاوية المنطقية لكل مفتاح قناة.
_state = {}


def _aux_cfg():
    return config.SENSORS["aux_servo"]


def _servo_cfg(which):
    return _aux_cfg().get(which, {})


def _limits(which):
    aux = _aux_cfg()
    sc = aux.get(which, {})
    lo = sc.get("min", aux.get("min", 0))
    hi = sc.get("max", aux.get("max", 180))
    return lo, hi


def _refresh_keys():
    """يحدّث خريطة الأسماء والحالة من الإعدادات (يُستدعى عند الإقلاع وإعادة التحميل)."""
    aux = _aux_cfg()
    _KEYS.clear()
    for which in ("camera", "soil"):
        key = aux.get(which, {}).get("key")
        if key:
            _KEYS[which] = key
            _state.setdefault(key, aux.get(which, {}).get("home", 90))


_refresh_keys()


def set_angle(which, angle):
    """يضبط زاوية سيرفو مساعد بالاسم المنطقي (camera|soil). يُرجع الزاوية بعد القصّ."""
    key = _KEYS.get(which)
    if not key:
        return None
    lo, hi = _limits(which)
    angle = max(lo, min(hi, int(round(float(angle)))))
    _state[key] = angle
    phys = (lo + hi - angle) if _servo_cfg(which).get("invert") else angle
    hardware.write_servo_raw(key, phys)   # يكتب مباشرة على قناة PCA المناسبة
    return angle


def set_by_name(which, angle):
    """which = 'camera' | 'soil'. يُرجع (ok, angle_or_error)."""
    if which not in _KEYS:
        return False, "which=camera|soil"
    return True, set_angle(which, angle)


def deploy_soil():
    """يُنزل حساس التربة لزاوية الغرز (lower_angle)."""
    return set_angle("soil", _servo_cfg("soil").get("lower_angle", 20))


def retract_soil():
    """يرفع حساس التربة لزاوية الرفع (raise_angle)."""
    return set_angle("soil", _servo_cfg("soil").get("raise_angle", 90))


def center_camera():
    """يعيد سيرفو الكاميرا لزاوية الوسط (home)."""
    return set_angle("camera", _servo_cfg("camera").get("home", 90))


def get_state():
    return {which: _state.get(key) for which, key in _KEYS.items()}
