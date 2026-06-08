# spider/hardware.py
"""طبقة الهاردوير: PCA9685 + قاطع تغذية GPIO + قفل I2C موحّد."""
import threading
import time

# ── إعداد PCA9685 ──
HARDWARE = False
right_pca = None
left_pca = None
try:
    from adafruit_servokit import ServoKit
    right_pca = ServoKit(channels=16, address=0x40)
    left_pca = ServoKit(channels=16, address=0x44)
    HARDWARE = True
except Exception:
    pass

# قنوات بزاوية موسّعة (>180°): نكتب PWM مباشرة بدل servo.angle
# تُبنى عند أول استدعاء ل _ensure_wide_init()
_wide_channels = {}  # {(side,ch): max_angle}
_wide_inited = False

# ── قاطع التغذية الفيزيائي (GPIO + MOSFET/Relay) ──
# ⚠️ وصّل بوابة MOSFET قناة-N (مثل IRLZ44N) أو Relay module على هذا الـ pin.
#    عند HIGH = التغذية واصلة، عند LOW = مقطوعة. (اعكس المنطق لو Relay فعّال-منخفض)
POWER_GPIO_PIN = 17          # ⚠️ عدّل حسب توصيلك
POWER_ACTIVE_HIGH = True     # ⚠️ True لو MOSFET، False لو Relay فعّال-منخفض

_gpio = None
try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(POWER_GPIO_PIN, GPIO.OUT)
    _gpio = GPIO
except Exception:
    _gpio = None

# ── قفل I2C موحّد لكل المشروع ──
i2c_lock = threading.Lock()

# حالة التغذية
_power_on = False
_power_lock = threading.Lock()


def power_enable():
    """يصل تغذية السيرفوات فيزيائياً."""
    global _power_on
    with _power_lock:
        if _gpio:
            try:
                _gpio.output(POWER_GPIO_PIN, GPIO.HIGH if POWER_ACTIVE_HIGH else GPIO.LOW)
            except Exception:
                pass
        _power_on = True


def power_cut():
    """يقطع تغذية كل السيرفوات فوراً — لا يمرّ عبر I2C إطلاقاً."""
    global _power_on
    with _power_lock:
        if _gpio:
            try:
                _gpio.output(POWER_GPIO_PIN, GPIO.LOW if POWER_ACTIVE_HIGH else GPIO.HIGH)
            except Exception:
                pass
        _power_on = False


def is_powered():
    return _power_on


def pwm_release_all():
    """يوقف إشارة PWM لكل القنوات (duty=0) — يكمّل القطع الفيزيائي."""
    if not HARDWARE:
        return
    with i2c_lock:
        try:
            for ch in range(16):
                right_pca._pca.channels[ch].duty_cycle = 0
                left_pca._pca.channels[ch].duty_cycle = 0
        except Exception:
            pass


def _ensure_wide_init():
    """يُهيّئ قنوات الزاوية الموسّعة من الإعدادات (مرة واحدة)."""
    global _wide_inited
    if _wide_inited:
        return
    _wide_inited = True
    try:
        from spider import config
        aux = config.SENSORS.get("aux_servo", {})
        for which in ("camera", "soil"):
            sc = aux.get(which, {})
            key = sc.get("key")
            lo = sc.get("min", aux.get("min", 0))
            hi = sc.get("max", aux.get("max", 180))
            if key and hi > 180:
                side = key[0]
                ch = int(key[1:])
                _wide_channels[(side, ch)] = hi
                pca = right_pca if side == "R" else left_pca
                if pca:
                    # تعطيل وضع الزاوية للسماح بكتابة duty مباشراً
                    pca.servo[ch].angle = None
    except Exception:
        pass


def _write_wide(pca, ch, angle, max_angle):
    """كتابة PWM مباشرة لقناة بزاوية >180°."""
    # نطاق Adafruit الافتراضي: 750µs (0°) → 2250µs (180°)
    # نمدّه خطياً لزاوية أكبر
    pulse_min = 750           # µs عند 0°
    rate = (2250 - 750) / 180  # µs لكل درجة
    angle = max(0.0, min(float(max_angle), float(angle)))
    pulse = pulse_min + rate * angle
    # Adafruit PCA9685: duty_cycle = 16-bit (0-65535)
    period_us = 1_000_000 / 50  # 50 Hz = 20ms
    duty = int(pulse / period_us * 65536)
    duty = max(0, min(65535, duty))
    pca._pca.channels[ch].duty_cycle = duty


def write_servo_raw(key, angle):
    """كتابة محرك واحد مباشرة — يُسخدم فقط من داخل MotionArbiter."""
    if not HARDWARE:
        return
    _ensure_wide_init()
    side = key[0]
    ch = int(key[1:])
    max_a = _wide_channels.get((side, ch))
    with i2c_lock:
        try:
            if max_a:
                # قناة بزاوية موسّعة — PWM مباشرة
                pca = right_pca if side == "R" else left_pca
                _write_wide(pca, ch, angle, max_a)
            else:
                if side == "R":
                    right_pca.servo[ch].angle = angle
                else:
                    left_pca.servo[ch].angle = angle
        except Exception:
            pass


def write_batch_raw(updates):
    """كتابة دفعة محركات تحت قفل واحد — يُستخدم فقط من داخل MotionArbiter."""
    if not HARDWARE:
        return
    _ensure_wide_init()
    with i2c_lock:
        try:
            for key, angle in updates.items():
                side = key[0]
                ch = int(key[1:])
                max_a = _wide_channels.get((side, ch))
                if max_a:
                    pca = right_pca if side == "R" else left_pca
                    _write_wide(pca, ch, angle, max_a)
                else:
                    if side == "R":
                        right_pca.servo[ch].angle = angle
                    else:
                        left_pca.servo[ch].angle = angle
        except Exception:
            pass
