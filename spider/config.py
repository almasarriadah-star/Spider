# spider/config.py
"""إعدادات المشروع وتحميل حدود المحركات."""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
LIMITS_FILE = os.path.join(CONFIG_DIR, "servo_limits.json")

_LIM_DEFAULT = {
    "default": {"min": 45, "max": 135},
    "overrides": {
        "L3": {"min": 60, "max": 120},
        "R3": {"min": 55, "max": 125}
    }
}


def _load_limits():
    try:
        with open(LIMITS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(_LIM_DEFAULT)


_LIM = _load_limits()


def servo_limit(key, angle):
    """حساب الحدود الآمنة لكل محرك."""
    d = _LIM["default"]
    o = _LIM["overrides"].get(key, {})
    lo = o.get("min", d["min"])
    hi = o.get("max", d["max"])
    return max(lo, min(hi, int(round(angle))))


def reload():
    """إعادة تحميل حدود المحركات حياً بلا إعادة تشغيل السيرفر."""
    global _LIM
    _LIM = _load_limits()
    print("[config] servo_limits reloaded")


# ════════════════════════════════════════════════════════════════
# ── منافذ الحساسات والأقطاب (راجع docs/HARDWARE_WIRING.md) ──
# ════════════════════════════════════════════════════════════════
IMU_PORT       = "/dev/serial0"  # UART0 — BNO085
LIDAR_PORT     = "/dev/ttyAMA2"  # UART3 — LD06
LIDAR_BAUD     = 230400          # LD06 دوّار 360°
LIDAR_KIND     = "ld06"          # بروتوكول LD06 (رأس 0x54 0x2C)
LIDAR_PWM_GPIO = 18              # BCM (p12) — تحكّم موتور دوران LD06
GPS_PORT       = "/dev/ttyAMA3"  # UART4
GPS_BAUD       = 9600
THERMAL_PORT   = "/dev/ttyAMA4"  # UART5 — موديول MLX90640 بخرج UART
THERMAL_BAUD   = 115200          # ⚠️ عدّل حسب ورقة الموديول
SOIL_PORT      = "/dev/ttyUSB0"  # رطوبة التربة عبر محوّل USB‑Serial
SOIL_BAUD      = 9600            # ⚠️ عدّل حسب الموديول

GAS_DO_GPIO    = 6               # BCM — خرج رقمي للغاز MQ135
GAS_ACTIVE_LOW = True            # True لو يعطي LOW عند الإنذار (فعّال-منخفض، شائع في MQ)
DHT22_GPIO     = 16              # BCM — خط بيانات DHT22 (حرارة + رطوبة الجو)

# سيرفوات مساعدة على PCA9685 (القنوات 9 الفاضية — الأرجل تستخدم 0–8)
CAM_SERVO_KEY  = "R9"            # SG90 — ميكانزم الكاميرا (لوحة اليمين 0x40)
SOIL_SERVO_KEY = "L9"            # DS3230 — ميكانزم التربة (لوحة الشمال 0x44)
