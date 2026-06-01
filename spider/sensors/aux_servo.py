# spider/sensors/aux_servo.py
"""تحكّم بسيرفوات مساعدة (كاميرا SG90 / تربة DS3230) على قنوات PCA9685 الفاضية.

هذه السيرفوات ليست أرجلاً فلا تمرّ عبر مولّد المشية — تُكتب مباشرة عبر طبقة الهاردوير
على القناتين R9/L9 بحدود زاوية مستقلّة عن أرجل المشية.
"""
from spider import hardware
from spider.config import CAM_SERVO_KEY, SOIL_SERVO_KEY, AUX_SERVO_MIN, AUX_SERVO_MAX

# حدود مستقلّة (SG90/DS3230 مدى أوسع من الأرجل) — تُضبط من config/sensors.json
_LIMITS = {CAM_SERVO_KEY: (AUX_SERVO_MIN, AUX_SERVO_MAX),
           SOIL_SERVO_KEY: (AUX_SERVO_MIN, AUX_SERVO_MAX)}
_state = {CAM_SERVO_KEY: 90, SOIL_SERVO_KEY: 90}

# تعيين اسم منطقي → مفتاح القناة
_KEYS = {"camera": CAM_SERVO_KEY, "soil": SOIL_SERVO_KEY}


def set_angle(key, angle):
    """يضبط زاوية سيرفو مساعد (key = R9/L9). يُرجع الزاوية بعد القصّ ضمن الحدود."""
    lo, hi = _LIMITS.get(key, (0, 180))
    angle = max(lo, min(hi, int(round(float(angle)))))
    _state[key] = angle
    hardware.write_servo_raw(key, angle)   # يكتب مباشرة على قناة PCA المناسبة
    return angle


def set_by_name(which, angle):
    """which = 'camera' | 'soil'. يُرجع (ok, angle_or_error)."""
    key = _KEYS.get(which)
    if not key:
        return False, "which=camera|soil"
    return True, set_angle(key, angle)


def get_state():
    return {"camera": _state[CAM_SERVO_KEY], "soil": _state[SOIL_SERVO_KEY]}
