from flask import Flask, render_template, request, jsonify
import json
import math
import os
import time
import threading

app = Flask(__name__)

# ── Hardware Setup ──
HARDWARE = False
try:
    from adafruit_servokit import ServoKit
    right_pca = ServoKit(channels=16, address=0x40)
    left_pca = ServoKit(channels=16, address=0x44)
    HARDWARE = True
except Exception:
    pass

# ── I2C Lock — حماية PCA9685 من threads المتعددة ──
i2c_lock = threading.Lock()

# ── BNO085 IMU Setup ──
BNO_READY = False
bno = None
bno_lock = threading.Lock()
bno_zero_roll = 0.0
bno_zero_pitch = 0.0

try:
    import serial
    from adafruit_bno08x_rvc import BNO08x_RVC
    uart = serial.Serial("/dev/serial0", 115200, timeout=0.5)
    bno = BNO08x_RVC(uart)
    BNO_READY = True
except Exception:
    pass


def read_bno_single():
    if not BNO_READY:
        return None
    acquired = bno_lock.acquire(timeout=0.2)
    if not acquired:
        return None
    try:
        old_timeout = uart.timeout
        uart.timeout = 0.1  # 100ms max — ما نعلّق أبداً
        try:
            yaw, pitch, roll, ax, ay, az = bno.heading
            return {
                "roll": round(roll - bno_zero_roll, 2),
                "pitch": round(pitch - bno_zero_pitch, 2),
                "yaw": round(yaw, 2),
                "raw_roll": round(roll, 2),
                "raw_pitch": round(pitch, 2),
                "ax": round(ax, 3),
                "ay": round(ay, 3),
                "az": round(az, 3)
            }
        except Exception:
            return None
        finally:
            uart.timeout = old_timeout
    finally:
        bno_lock.release()

# ── Constants ──
MIN_ANGLE = 45
MAX_ANGLE = 135

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

SERVO_NAMES = {
    "R0": "RF Coxa",  "R1": "RF Femur",  "R2": "RF Tibia",
    "R3": "RM Coxa",  "R4": "RM Femur",  "R5": "RM Tibia",
    "R6": "RR Coxa",  "R7": "RR Femur",  "R8": "RR Tibia",
    "L0": "LF Coxa",  "L1": "LF Femur",  "L2": "LF Tibia",
    "L3": "LM Coxa",  "L4": "LM Femur",  "L5": "LM Tibia",
    "L6": "LR Coxa",  "L7": "LR Femur",  "L8": "LR Tibia",
}

# Leg groups: group_name -> [servo_keys]
LEG_GROUPS = {
    "RF": ["R0", "R1", "R2"],
    "RM": ["R3", "R4", "R5"],
    "RR": ["R6", "R7", "R8"],
    "LF": ["L0", "L1", "L2"],
    "LM": ["L3", "L4", "L5"],
    "LR": ["L6", "L7", "L8"],
}

BASE_DIR = os.path.dirname(__file__)
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
DEFAULTS_FILE = os.path.join(BASE_DIR, "leg_defaults.json")
PRESETS_FILE = os.path.join(BASE_DIR, "leg_presets.json")

# ── Gait Planner Storage ──
GAIT_FILE = os.path.join(BASE_DIR, "gait_params.json")
BALANCE_CONFIG_FILE = os.path.join(BASE_DIR, "balance_config.json")
gait_running = False
gait_event = threading.Event()  # thread-safe replacement
gait_thread = None
gait_lock = threading.Lock()
gait_step_count = 0
gait_phase = "A"
gait_current_phase = "idle"

# ── Current State ──
current = {}
for ch in range(9):
    current[f"R{ch}"] = DEFAULT_RIGHT[ch]
    current[f"L{ch}"] = DEFAULT_LEFT[ch]

# ── علامة: هل المحركات شغّالة ──
_servos_initialized = False


def limit_angle(a):
    return max(MIN_ANGLE, min(MAX_ANGLE, int(a)))


def set_servo(key, angle):
    _ensure_servos_on()
    angle = limit_angle(angle)
    side = key[0]
    ch = int(key[1:])
    if HARDWARE:
        with i2c_lock:
            if side == "R":
                right_pca.servo[ch].angle = angle
            else:
                left_pca.servo[ch].angle = angle
    current[key] = angle
    return angle


def set_servos_batch(updates):
    """يكتب عدة محركات دفعة واحدة ضمن قفل واحد — أسرع بكثير"""
    _ensure_servos_on()
    right_writes = {}
    left_writes = {}
    for key, angle in updates.items():
        angle = limit_angle(angle)
        side = key[0]
        ch = int(key[1:])
        if side == "R":
            right_writes[ch] = angle
        else:
            left_writes[ch] = angle
        current[key] = angle
    if HARDWARE:
        with i2c_lock:
            for ch, angle in right_writes.items():
                right_pca.servo[ch].angle = angle
            for ch, angle in left_writes.items():
                left_pca.servo[ch].angle = angle


def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_positions():
    return load_json(POSITIONS_FILE)


def save_positions(data):
    save_json(POSITIONS_FILE, data)


def load_leg_defaults():
    return load_json(DEFAULTS_FILE)


def save_leg_defaults(data):
    save_json(DEFAULTS_FILE, data)


def load_leg_presets():
    return load_json(PRESETS_FILE)


def save_leg_presets(data):
    save_json(PRESETS_FILE, data)


# ── Lazy Servo Initialization ──
def apply_startup_calibration():
    """يحرّك المحركات لوضعية التوازن — ينطبق فقط أول مرة يُطلب فيها"""
    global _servos_initialized
    if not HARDWARE or _servos_initialized:
        return
    defaults = load_leg_defaults()
    print("Applying startup calibration...")
    with i2c_lock:
        for group, keys in LEG_GROUPS.items():
            for key in keys:
                base = DEFAULT_RIGHT[int(key[1:])] if key[0] == "R" else DEFAULT_LEFT[int(key[1:])]
                val = defaults.get(group, {}).get(key, base)
                side = key[0]
                ch = int(key[1:])
                if side == "R":
                    right_pca.servo[ch].angle = val
                else:
                    left_pca.servo[ch].angle = val
                current[key] = val
                time.sleep(0.03)
    _servos_initialized = True
    print("Startup calibration applied - servos holding position")


def _ensure_servos_on():
    """تأكد إن المحركات شغّالة — أول command يفعّلها"""
    if not _servos_initialized:
        apply_startup_calibration()


# ── Gait Helpers ──
# This flag forces ALL movement to stop immediately
_force_stop = False

# ────── Easing Functions ──────
def ease_smoothstep(t):
    """Hermite smoothstep — بداية ونهاية ناعمة"""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def ease_cosine(t):
    """Cosine ease — أنعم من smoothstep"""
    return (1 - math.cos(t * math.pi)) / 2

# الدالة الافتراضية
_ease_func = ease_smoothstep


def smooth_move_leg(keys, targets, steps=10, delay=0.03, easing=None):
    """Smoothly move a set of servos to target angles with easing. Respects _force_stop."""
    global _force_stop
    if easing is None:
        easing = _ease_func
    starts = {k: current[k] for k in keys}
    for step in range(1, steps + 1):
        if _force_stop:
            return
        t = step / steps
        t_eased = easing(t)
        batch = {}
        for k in keys:
            if k in targets:
                angle = starts[k] + (targets[k] - starts[k]) * t_eased
                batch[k] = round(angle)
        set_servos_batch(batch)  # ← قفل واحد بدل 18
        time.sleep(delay)


def get_leg_angles(leg_name, frame, params, gait_type='forward'):
    """
    حساب زوايا الرجل لأي فريم في دورة المشي.
    يعيد dict مثل {"R0": 95, "R1": 110, "R2": 91}
    يدعم أنواع حركة مختلفة عبر gait_type.
    """
    leg = params["legs"][leg_name]
    total = params["total_frames"]
    amp = params["coxa_amplitude"]
    grp_a = params["groups"]["A"]

    # offset نصف دورة للمجموعة B
    offset = 0 if leg_name in grp_a else total // 2
    f = (frame + offset) % total
    half = total // 2
    side = leg["side"]  # "R" أو "L"

    # اتجاه Coxa: اليمين زيادة=أمام، اليسار نقصان=أمام
    direction = 1 if side == "R" else -1

    # ── تعديل الاتجاه حسب نوع الحركة ──
    if gait_type == 'backward':
        direction = -direction

    if gait_type == 'turn_left':
        if side == 'L':
            direction = -direction

    if gait_type == 'turn_right':
        if side == 'R':
            direction = -direction

    # ── الانزياح والزحف الجانبي ──
    coxa_angle_offset = 0

    # الانزياح (Shift) صار ثابت - فقط نزيح الزاوية بدون مشي للأمام
    if gait_type == 'shift_left':
        coxa_angle_offset = -18
        direction = 0  # إلغاء المشي للأمام
    elif gait_type == 'shift_right':
        coxa_angle_offset = 18
        direction = 0

    # الزحف السرطاني (Strafe / Crab Walk)
    # نوجه الجسم بزاوية ثم نمشي للأمام، فيعطي حركة قطرية (Crab Walk)
    elif gait_type == 'strafe_left' or gait_type == 'crab_walk':
        coxa_angle_offset = -20
        # direction يبقى طبيعي (1 لليمين، -1 لليسار) ليمشي للأمام وهو مائل
    elif gait_type == 'strafe_right':
        coxa_angle_offset = 20

    # ── تعديل الارتفاع للتضاريس ──
    femur_lift_extra = 0
    if gait_type == 'climb':
        femur_lift_extra = 15

    if f < half:
        # ── SWING phase ──────────────────────────────
        progress = f / half  # 0.0 → 1.0
        coxa = leg["coxa_stand"] + direction * (-amp + 2 * amp * progress) + coxa_angle_offset
        # femur يرسم قوس باستخدام sin → ناعم وطبيعي
        femur = leg["femur_stand"] + (leg["femur_lift"] + femur_lift_extra - leg["femur_stand"]) \
                * math.sin(progress * math.pi)
    else:
        # ── STANCE phase ─────────────────────────────
        progress = (f - half) / half  # 0.0 → 1.0
        coxa = leg["coxa_stand"] + direction * (amp - 2 * amp * progress) + coxa_angle_offset
        femur = leg["femur_stand"]  # ثابت على الأرض

    tibia = leg["tibia"]  # ما بيتحرك

    # بناء مفاتيح المحركات
    legs_order = list(params["legs"].keys())
    leg_idx = legs_order.index(leg_name)
    if side == "R":
        ch = leg_idx * 3
    else:
        ch = (leg_idx - 3) * 3

    c_key = f"{side}{ch}"
    f_key = f"{side}{ch+1}"
    t_key = f"{side}{ch+2}"

    return {
        c_key: max(45, min(135, round(coxa))),
        f_key: max(45, min(135, round(femur))),
        t_key: max(45, min(135, round(tibia))),
    }


def execute_gait(params, speed, max_steps):
    """Execute tripod gait in background thread"""
    global gait_running, gait_step_count, gait_phase
    # Tripod groups
    GROUP_A = ["RF", "LM", "LR"]  # legs 1,3,5
    GROUP_B = ["LF", "RM", "RR"]  # legs 2,4,6

    gait_step_count = 0
    base_steps = 10  # interpolation steps
    base_delay = 0.03

    actual_steps = max(3, int(base_steps / speed))
    actual_delay = max(0.01, base_delay / speed)

    while gait_event.is_set() and (max_steps == 0 or gait_step_count < max_steps):
        # Phase 1: Group A swings forward, Group B pushes back
        gait_phase = "A"
        # Group A: stance -> lift -> swing_fwd
        for group in GROUP_A:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("lift", params[group].get("stance", {})), actual_steps, actual_delay)
                smooth_move_leg(leg_keys, params[group].get("swing_fwd", params[group].get("stance", {})), actual_steps, actual_delay)
        # Group B: stance -> push_back (already on ground, push back)
        for group in GROUP_B:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("push_back", params[group].get("stance", {})), actual_steps, actual_delay)

        gait_step_count += 1
        if not gait_event.is_set():
            break

        # Phase 2: Group B swings forward, Group A pushes back
        gait_phase = "B"
        # Group B: stance -> lift -> swing_fwd
        for group in GROUP_B:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("lift", params[group].get("stance", {})), actual_steps, actual_delay)
                smooth_move_leg(leg_keys, params[group].get("swing_fwd", params[group].get("stance", {})), actual_steps, actual_delay)
        # Group A: stance -> push_back
        for group in GROUP_A:
            if group in params:
                leg_keys = LEG_GROUPS[group]
                smooth_move_leg(leg_keys, params[group].get("push_back", params[group].get("stance", {})), actual_steps, actual_delay)

        gait_step_count += 1

    # Return to stance
    for group in list(GROUP_A) + list(GROUP_B):
        if group in params:
            smooth_move_leg(LEG_GROUPS[group], params[group].get("stance", {}), actual_steps, actual_delay)

    gait_running = False
    gait_event.clear()


def _load_gait_params(name="forward_tripod"):
    """يحمّل خطة المشي من gait_params.json."""
    try:
        with open(GAIT_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            for plan in data:
                if plan.get("gait") == name:
                    return plan
        elif isinstance(data, dict) and data.get("gait") == name:
            return data
    except Exception:
        pass
    return None


def _run_forward_gait(params, speed=1.0, max_cycles=0, gait_type='forward'):
    """تنفيذ Forward Tripod Gait في thread خلفية."""
    global gait_running, gait_step_count, gait_current_phase

    # ── تعديلات خاصة لبعض أنواع الحركة ──
    effective_speed = speed
    if gait_type == 'creep':
        effective_speed = speed * 0.3

    delay = (params["frame_delay_ms"] / 1000.0) / effective_speed
    s_steps = max(3, int(params["smooth_steps"] / effective_speed))
    s_delay = max(0.01, params["smooth_delay"] / effective_speed)
    total = params["total_frames"]
    legs = list(params["legs"].keys())

    frame = 0
    cycles = 0

    while gait_event.is_set():
        if max_cycles > 0 and cycles >= max_cycles:
            break

        # حساب زوايا كل الأرجل لهذا الفريم
        all_targets = {}
        for leg in legs:
            angles = get_leg_angles(leg, frame, params, gait_type=gait_type)
            all_targets.update(angles)

        # ── Balance correction للأرجل على الأرض (Tripod) ──
        if balance.enabled:
            grp_a = params["groups"]["A"]
            half = total // 2
            f_a = frame % total
            # Group A بيرتفع بالنص الأول, Group B بالنص الثاني
            for leg_name in legs:
                leg = params["legs"][leg_name]
                side = leg["side"]
                legs_order = list(params["legs"].keys())
                leg_idx = legs_order.index(leg_name)
                if side == "R":
                    ch = leg_idx * 3
                else:
                    ch = (leg_idx - 3) * 3
                femur_key = f"{side}{ch+1}"
                # هل الرجل بمرحلة swing؟
                offset = 0 if leg_name in grp_a else half
                f_leg = (f_a + offset) % total
                is_swing = f_leg < half
                if not is_swing and femur_key in all_targets:
                    correction = balance.femur_offsets.get(leg_name, 0.0)
                    all_targets[femur_key] = max(45, min(135, round(all_targets[femur_key] + correction)))

        # تطبيق — كل الأرجل معاً
        gait_current_phase = f"frame-{frame}"
        smooth_move_leg(list(all_targets.keys()), all_targets, steps=s_steps, delay=s_delay)

        frame += 1
        if frame >= total:
            frame = 0
            cycles += 1
            gait_step_count += 1

        remaining = delay - s_steps * s_delay
        time.sleep(max(0, remaining))

    # إيقاف نظيف — ارجع لوضعية الوقوف
    gait_current_phase = "returning"
    stance_targets = {}
    for leg_name, leg_data in params["legs"].items():
        side = leg_data["side"]
        leg_idx = list(params["legs"].keys()).index(leg_name)
        if side == "R":
            ch = leg_idx * 3
        else:
            ch = (leg_idx - 3) * 3
        stance_targets[f"{side}{ch}"] = leg_data["coxa_stand"]
        stance_targets[f"{side}{ch+1}"] = leg_data["femur_stand"]
        stance_targets[f"{side}{ch+2}"] = leg_data["tibia"]

    smooth_move_leg(list(stance_targets.keys()), stance_targets, steps=12, delay=0.03)
    gait_current_phase = "idle"
    gait_running = False
    gait_event.clear()


# ════════════════════════════════════════════════════════════════
# ── Balance System (Gimbal Balance + PID) ──
# ════════════════════════════════════════════════════════════════

class SimplePID:
    """PID controller بسيط مع anti-windup"""
    def __init__(self, kp=0.8, ki=0.0, kd=0.3, output_limit=12.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_limit = output_limit
        self._prev_error = 0.0
        self._integral = 0.0
        self._last_time = None

    def reset(self):
        self._prev_error = 0.0
        self._integral = 0.0
        self._last_time = None

    def update(self, error):
        now = time.monotonic()
        if self._last_time is None:
            dt = 0.033  # ~30Hz افتراضي
        else:
            dt = now - self._last_time
            dt = max(dt, 0.001)  # حماية من dt=0
        self._last_time = now

        # P
        p_term = self.kp * error

        # I مع anti-windup
        self._integral += error * dt
        if self.ki > 0:
            max_integral = self.output_limit / self.ki
            self._integral = max(-max_integral, min(max_integral, self._integral))
        i_term = self.ki * self._integral

        # D
        derivative = (error - self._prev_error) / dt
        d_term = self.kd * derivative
        self._prev_error = error

        # مجموع مع clamp
        output = p_term + i_term + d_term
        return max(-self.output_limit, min(self.output_limit, output))


# ── أوزان توزيع التوازن لكل رجل ──
BALANCE_WEIGHTS = {
    # (roll_weight, pitch_weight)
    "RF": (+0.5, -0.5),   # يمين أمامي
    "RM": (+0.5,  0.0),   # يمين وسطي
    "RR": (+0.5, +0.5),   # يمين خلفي
    "LF": (-0.5, -0.5),   # يسار أمامي
    "LM": (-0.5,  0.0),   # يسار وسطي
    "LR": (-0.5, +0.5),   # يسار خلفي
}

# ── خريطة القنوات الكاملة ──
LEG_COXA_CHANNEL = {
    "RF": "R0", "RM": "R3", "RR": "R6",
    "LF": "L0", "LM": "L3", "LR": "L6",
}
LEG_FEMUR_CHANNEL = {
    "RF": "R1", "RM": "R4", "RR": "R7",
    "LF": "L1", "LM": "L4", "LR": "L7",
}
LEG_TIBIA_CHANNEL = {
    "RF": "R2", "RM": "R5", "RR": "R8",
    "LF": "L2", "LM": "L5", "LR": "L8",
}

# اتجاه Femur (+1 = زيادة الزاوية تنزّل الرجل, -1 = العكس)
FEMUR_DIRECTION = {
    "RF": -1, "RM": -1, "RR": -1,
    "LF": +1, "LM": +1, "LR": +1,
}


class BalanceController:
    def __init__(self):
        try:
            with open(BALANCE_CONFIG_FILE) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        roll_cfg = cfg.get("roll_pid", {})
        pitch_cfg = cfg.get("pitch_pid", {})

        self.roll_pid = SimplePID(
            kp=roll_cfg.get("kp", 0.8),
            ki=roll_cfg.get("ki", 0.0),
            kd=roll_cfg.get("kd", 0.3),
            output_limit=cfg.get("max_correction", 12)
        )
        self.pitch_pid = SimplePID(
            kp=pitch_cfg.get("kp", 0.8),
            ki=pitch_cfg.get("ki", 0.0),
            kd=pitch_cfg.get("kd", 0.3),
            output_limit=cfg.get("max_correction", 12)
        )

        self.max_correction = cfg.get("max_correction", 12)
        self.frequency = cfg.get("frequency_hz", 30)
        self.smoothing_n = cfg.get("imu_smoothing_samples", 5)
        self.deadzone = cfg.get("deadzone_deg", 1.5)

        self.enabled = False
        self._thread = None
        self._stop_event = threading.Event()

        # آخر قيم (للـ API)
        self.last_roll = 0.0
        self.last_pitch = 0.0
        self.last_roll_correction = 0.0
        self.last_pitch_correction = 0.0

        # تاريخ للـ debug (آخر 100 عينة)
        self.debug_history = []
        self.MAX_HISTORY = 100

        # IMU smoothing buffer
        self._roll_buf = []
        self._pitch_buf = []

        # التصحيحات الحالية لكل رجل (يقرأها الـ gait engine)
        self.femur_offsets = {leg: 0.0 for leg in BALANCE_WEIGHTS}

        # آخر زوايا Femur أساسية (قبل التصحيح) — للوقوف
        self._base_femur = {}

    def start(self):
        if self.enabled:
            return
        self.enabled = True
        self._stop_event.clear()
        self.roll_pid.reset()
        self.pitch_pid.reset()
        self._roll_buf.clear()
        self._pitch_buf.clear()
        # حفظ الزوايا الأساسية الحالية
        self._base_femur = {leg: current[LEG_FEMUR_CHANNEL[leg]] for leg in BALANCE_WEIGHTS}
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.enabled = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        # صفّر التصحيحات وارجع للزوايا الأساسية
        for leg in BALANCE_WEIGHTS:
            ch = LEG_FEMUR_CHANNEL[leg]
            if ch in current:
                base = self._base_femur.get(leg, current[ch])
                set_servo(ch, base)
        self.femur_offsets = {leg: 0.0 for leg in BALANCE_WEIGHTS}
        self.last_roll_correction = 0.0
        self.last_pitch_correction = 0.0

    def _smooth(self, buf, new_val, n):
        """Moving average بسيط"""
        buf.append(new_val)
        if len(buf) > n:
            buf.pop(0)
        return sum(buf) / len(buf)

    def _loop(self):
        interval = 1.0 / self.frequency
        while not self._stop_event.is_set():
            loop_start = time.monotonic()
            try:
                self._balance_tick()
            except Exception as e:
                print(f"[Balance] Error: {e}")
            elapsed = time.monotonic() - loop_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                self._stop_event.wait(sleep_time)

    def _balance_tick(self):
        # 1. اقرأ IMU — read_bno_single بياخد bno_lock داخلياً
        imu = read_bno_single()

        if imu is None:
            return

        raw_roll = imu["roll"]
        raw_pitch = imu["pitch"]

        # 2. Smoothing
        roll = self._smooth(self._roll_buf, raw_roll, self.smoothing_n)
        pitch = self._smooth(self._pitch_buf, raw_pitch, self.smoothing_n)

        self.last_roll = round(roll, 2)
        self.last_pitch = round(pitch, 2)

        # 3. Deadzone — تجاهل الانحرافات الصغيرة
        error_roll = 0.0 if abs(roll) < self.deadzone else -roll
        error_pitch = 0.0 if abs(pitch) < self.deadzone else -pitch

        # 4. PID
        corr_roll = self.roll_pid.update(error_roll)
        corr_pitch = self.pitch_pid.update(error_pitch)

        self.last_roll_correction = round(corr_roll, 2)
        self.last_pitch_correction = round(corr_pitch, 2)

        # 5. وزّع على الأرجل
        for leg, (rw, pw) in BALANCE_WEIGHTS.items():
            offset = (corr_roll * rw + corr_pitch * pw)
            direction = FEMUR_DIRECTION[leg]
            self.femur_offsets[leg] = round(offset * direction, 1)

        # 6. طبّق التصحيح (فقط إذا ما في مشي شغّال)
        if not gait_event.is_set():
            self._apply_corrections()

        # 7. سجّل للـ debug
        entry = {
            "t": round(time.monotonic(), 3),
            "roll": self.last_roll,
            "pitch": self.last_pitch,
            "corr_r": self.last_roll_correction,
            "corr_p": self.last_pitch_correction,
        }
        self.debug_history.append(entry)
        if len(self.debug_history) > self.MAX_HISTORY:
            self.debug_history.pop(0)

    def _apply_corrections(self):
        """طبّق التصحيحات على السيرفوهات — للوقوف فقط"""
        for leg, offset in self.femur_offsets.items():
            ch = LEG_FEMUR_CHANNEL[leg]
            base = self._base_femur.get(leg, 90)
            target = base + offset
            # حدود أمان
            target = max(50, min(130, target))
            set_servo(ch, round(target))

    def tune(self, kp=None, ki=None, kd=None, axis="both"):
        """ضبط PID أثناء العمل"""
        pids = []
        if axis in ("both", "roll"):
            pids.append(self.roll_pid)
        if axis in ("both", "pitch"):
            pids.append(self.pitch_pid)
        for pid in pids:
            if kp is not None: pid.kp = kp
            if ki is not None: pid.ki = ki
            if kd is not None: pid.kd = kd

    def get_status(self):
        return {
            "enabled": self.enabled,
            "roll": self.last_roll,
            "pitch": self.last_pitch,
            "roll_correction": self.last_roll_correction,
            "pitch_correction": self.last_pitch_correction,
            "femur_offsets": self.femur_offsets,
            "pid_roll": {"kp": self.roll_pid.kp, "ki": self.roll_pid.ki, "kd": self.roll_pid.kd},
            "pid_pitch": {"kp": self.pitch_pid.kp, "ki": self.pitch_pid.ki, "kd": self.pitch_pid.kd},
        }


# ── إنشاء الـ controller ──
balance = BalanceController()


# ── Routes ──
@app.route("/")
def main_page():
    return render_template("main.html")

@app.route("/motors")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_status():
    defaults = load_leg_defaults()
    status = {}
    for key in current:
        group = None
        for g, keys in LEG_GROUPS.items():
            if key in keys:
                group = g
                break
        base_default = (DEFAULT_RIGHT if key[0] == "R" else DEFAULT_LEFT).get(int(key[1:]), 90)
        # Check if user has overridden default for this leg
        custom_default = None
        if group and group in defaults:
            custom_default = defaults[group].get(key)
        final_default = custom_default if custom_default is not None else base_default
        status[key] = {
            "angle": current[key],
            "name": SERVO_NAMES.get(key, key),
            "default": final_default
        }
    return jsonify({
        "servos": status,
        "hardware": HARDWARE,
        "min": MIN_ANGLE,
        "max": MAX_ANGLE,
        "leg_groups": LEG_GROUPS,
        "defaults": defaults
    })


@app.route("/api/servo", methods=["POST"])
def api_set_servo():
    data = request.json
    key = data.get("key", "")
    angle = data.get("angle")
    if key in current and angle is not None:
        a = set_servo(key, angle)
        return jsonify({"ok": True, "key": key, "angle": a})
    return jsonify({"ok": False, "error": "invalid"}), 400


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """تصفير _force_stop — طوارئ إذا ظل True"""
    global _force_stop
    _force_stop = False
    gait_event.clear()
    return jsonify({"ok": True, "message": "force_stop cleared"})


@app.route("/api/health", methods=["GET"])
def api_health():
    """فحص سريع لحالة النظام"""
    return jsonify({
        "ok": True,
        "imu": "connected" if BNO_READY else "disconnected",
        "gait_running": gait_event.is_set(),
        "force_stop": _force_stop,
        "servos_initialized": _servos_initialized,
        "balance": balance.enabled
    })


@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    defaults = load_leg_defaults()
    for ch in range(9):
        rk = f"R{ch}"
        lk = f"L{ch}"
        rv = defaults.get("RF", {}).get(rk) if rk in LEG_GROUPS.get("RF", []) else None
        lv = defaults.get("LF", {}).get(lk) if lk in LEG_GROUPS.get("LF", []) else None
        # Find the right group for each channel
        r_default = DEFAULT_RIGHT[ch]
        l_default = DEFAULT_LEFT[ch]
        for g, keys in LEG_GROUPS.items():
            if rk in keys and g in defaults:
                r_default = defaults[g][rk]
            if lk in keys and g in defaults:
                l_default = defaults[g][lk]
        set_servo(rk, r_default)
        set_servo(lk, l_default)
    return jsonify({"ok": True, "msg": "Default calibration applied"})


@app.route("/api/off", methods=["POST"])
def api_off():
    global gait_running, _force_stop
    try:
        _force_stop = True
        gait_running = False
        gait_event.clear()
        time.sleep(0.1)
    finally:
        _force_stop = False  # ← مضمون التنفيذ

    if HARDWARE:
        try:
            # 2. رجّع لوضعية التوازن أولاً (حركة سلسة)
            stance = dict(DEFAULT_RIGHT)
            stance.update(DEFAULT_LEFT)
            targets = {}
            for ch, angle in stance.items():
                if ch <= 8:
                    targets[f"R{ch}"] = angle
                else:
                    targets[f"L{ch-9}"] = angle
            smooth_move_leg(list(targets.keys()), targets, steps=8, delay=0.03)
            time.sleep(0.1)
        except Exception:
            pass

        try:
            # 3. ضبط كل القنوات لـ 0 — يوقف PWM بدون رجفة
            for ch in range(16):
                right_pca._pca.channels[ch].duty_cycle = 0
                left_pca._pca.channels[ch].duty_cycle = 0
        except Exception:
            pass
    return jsonify({"ok": True, "msg": "Balanced stance then servos off"})


# ── Per-Leg Default Routes ──
@app.route("/api/leg/defaults", methods=["GET"])
def api_leg_defaults():
    return jsonify({"defaults": load_leg_defaults()})


@app.route("/api/leg/default/apply", methods=["POST"])
def api_leg_default_apply():
    """Reset one leg group to its default values."""
    data = request.json
    group = data.get("group", "")
    if group not in LEG_GROUPS:
        return jsonify({"ok": False, "error": "invalid group"}), 400
    defaults = load_leg_defaults()
    for key in LEG_GROUPS[group]:
        val = DEFAULT_RIGHT[int(key[1:])] if key[0] == "R" else DEFAULT_LEFT[int(key[1:])]
        if group in defaults and key in defaults[group]:
            val = defaults[group][key]
        set_servo(key, val)
    return jsonify({"ok": True, "msg": f"Group {group} reset to default"})


@app.route("/api/leg/default/set", methods=["POST"])
def api_leg_default_set():
    """Set current position of one leg group as new default."""
    data = request.json
    group = data.get("group", "")
    if group not in LEG_GROUPS:
        return jsonify({"ok": False, "error": "invalid group"}), 400
    defaults = load_leg_defaults()
    defaults[group] = {key: current[key] for key in LEG_GROUPS[group]}
    save_leg_defaults(defaults)
    return jsonify({"ok": True, "msg": f"Group {group} default saved", "values": defaults[group]})


# ── Per-Leg Preset Routes (1, 2, 3) ──
@app.route("/api/leg/presets", methods=["GET"])
def api_leg_presets():
    return jsonify({"presets": load_leg_presets()})


@app.route("/api/leg/preset/save", methods=["POST"])
def api_leg_preset_save():
    """Save current position of a leg group into preset slot (1-3)."""
    data = request.json
    group = data.get("group", "")
    slot = str(data.get("slot", ""))
    if group not in LEG_GROUPS or slot not in ("1", "2", "3"):
        return jsonify({"ok": False, "error": "invalid group or slot"}), 400
    presets = load_leg_presets()
    if group not in presets:
        presets[group] = {}
    presets[group][slot] = {key: current[key] for key in LEG_GROUPS[group]}
    save_leg_presets(presets)
    return jsonify({"ok": True, "msg": f"Group {group} preset {slot} saved", "values": presets[group][slot]})


@app.route("/api/leg/preset/load", methods=["POST"])
def api_leg_preset_load():
    """Load a preset for a leg group."""
    data = request.json
    group = data.get("group", "")
    slot = str(data.get("slot", ""))
    if group not in LEG_GROUPS or slot not in ("1", "2", "3"):
        return jsonify({"ok": False, "error": "invalid group or slot"}), 400
    presets = load_leg_presets()
    if group not in presets or slot not in presets[group]:
        return jsonify({"ok": False, "error": "preset not found"}), 404
    for key, angle in presets[group][slot].items():
        if key in current:
            set_servo(key, angle)
    return jsonify({"ok": True, "msg": f"Group {group} preset {slot} loaded"})


# ── Global Positions ──
@app.route("/api/positions", methods=["GET"])
def api_list_positions():
    return jsonify({"positions": load_positions()})


@app.route("/api/positions/save", methods=["POST"])
def api_save_position():
    data = request.json
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    positions = load_positions()
    positions[name] = dict(current)
    save_positions(positions)
    return jsonify({"ok": True, "msg": f"Position '{name}' saved"})


@app.route("/api/positions/load", methods=["POST"])
def api_load_position():
    data = request.json
    name = data.get("name", "").strip()
    positions = load_positions()
    if name not in positions:
        return jsonify({"ok": False, "error": "not found"}), 404
    pos = positions[name]
    for key, angle in pos.items():
        if key in current:
            set_servo(key, angle)
    return jsonify({"ok": True, "msg": f"Position '{name}' loaded"})


@app.route("/api/positions/delete", methods=["POST"])
def api_delete_position():
    data = request.json
    name = data.get("name", "").strip()
    positions = load_positions()
    if name in positions:
        del positions[name]
        save_positions(positions)
        return jsonify({"ok": True, "msg": f"Position '{name}' deleted"})
    return jsonify({"ok": False, "error": "not found"}), 404


# ── Easing Routes ──
@app.route('/api/easing/set', methods=['POST'])
def set_easing():
    global _ease_func
    data = request.json or {}
    name = data.get("type", "smoothstep")
    if name == "linear":
        _ease_func = lambda t: t
    elif name == "cosine":
        _ease_func = ease_cosine
    elif name == "smoothstep":
        _ease_func = ease_smoothstep
    else:
        return jsonify({"error": f"Unknown easing: {name}"}), 400
    return jsonify({"easing": name})

@app.route('/api/easing/get', methods=['GET'])
def get_easing():
    name = _ease_func.__name__ if hasattr(_ease_func, '__name__') else "custom"
    return jsonify({"easing": name})


# ── Balance Routes ──
@app.route('/api/balance/start', methods=['POST'])
def balance_start():
    data = request.json or {}
    kp = data.get("kp")
    ki = data.get("ki")
    kd = data.get("kd")
    if any(v is not None for v in [kp, ki, kd]):
        balance.tune(kp=kp, ki=ki, kd=kd)
    balance.start()
    return jsonify({"status": "started", **balance.get_status()})

@app.route('/api/balance/stop', methods=['POST'])
def balance_stop():
    balance.stop()
    return jsonify({"status": "stopped"})

@app.route('/api/balance/status', methods=['GET'])
def balance_status():
    return jsonify(balance.get_status())

@app.route('/api/balance/tune', methods=['POST'])
def balance_tune():
    data = request.json or {}
    balance.tune(
        kp=data.get("kp"),
        ki=data.get("ki"),
        kd=data.get("kd"),
        axis=data.get("axis", "both")
    )
    return jsonify({"status": "tuned", **balance.get_status()})

@app.route('/api/balance/debug', methods=['GET'])
def balance_debug():
    return jsonify({"history": balance.debug_history})


# ── BNO / IMU Routes ──
@app.route("/api/imu/status", methods=["GET"])
def api_imu_status():
    return jsonify({"ready": BNO_READY})


@app.route("/api/imu/read", methods=["GET"])
def api_imu_read():
    reading = read_bno_single()
    if reading is None:
        return jsonify({"ok": False, "error": "BNO not available"})
    return jsonify({"ok": True, **reading, "zero_roll": bno_zero_roll, "zero_pitch": bno_zero_pitch})


@app.route("/api/imu/zero", methods=["POST"])
def api_imu_zero():
    global bno_zero_roll, bno_zero_pitch
    rolls, pitches = [], []
    for _ in range(20):
        r = read_bno_single()  # بياخد bno_lock داخلياً
        if r:
            rolls.append(r["raw_roll"])
            pitches.append(r["raw_pitch"])
        time.sleep(0.06)
    if not rolls:
        return jsonify({"ok": False, "error": "Cannot read BNO"})
    bno_zero_roll = sum(rolls) / len(rolls)
    bno_zero_pitch = sum(pitches) / len(pitches)
    return jsonify({
        "ok": True,
        "zero_roll": round(bno_zero_roll, 2),
        "zero_pitch": round(bno_zero_pitch, 2)
    })


@app.route("/api/imu/stream", methods=["GET"])
def api_imu_stream():
    count = int(request.args.get("count", 30))
    samples = []
    for _ in range(count):
        r = read_bno_single()  # بياخد bno_lock داخلياً
        if r:
            r["t"] = round(time.time() * 1000)
            samples.append(r)
        time.sleep(0.05)
    return jsonify({"ok": True, "samples": samples, "zero_roll": bno_zero_roll, "zero_pitch": bno_zero_pitch})


# ── Gait Planner Routes ──
@app.route("/gait")
def gait_page():
    return render_template("spider_gait_controller.html")

@app.route("/sim")
def spider_sim():
    return render_template("spider_sim.html")


@app.route("/api/gait/params", methods=["GET"])
def api_gait_params():
    """Load all saved gait parameters"""
    return jsonify({"plans": load_json(GAIT_FILE)})


@app.route("/api/gait/params/save", methods=["POST"])
def api_gait_params_save():
    """Save gait parameters as a named plan"""
    data = request.json
    name = data.get("name", "").strip()
    params = data.get("params", {})
    if not name or not params:
        return jsonify({"ok": False, "error": "name and params required"}), 400
    plans = load_json(GAIT_FILE)
    plans[name] = {"params": params}
    save_json(GAIT_FILE, plans)
    return jsonify({"ok": True, "msg": f"Plan '{name}' saved"})


@app.route("/api/gait/params/load", methods=["POST"])
def api_gait_params_load():
    """Load a named gait plan"""
    data = request.json
    name = data.get("name", "").strip()
    plans = load_json(GAIT_FILE)
    if name not in plans:
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    return jsonify({"ok": True, "name": name, "params": plans[name]["params"]})


@app.route("/api/gait/plans", methods=["GET"])
def api_gait_plans():
    """List all saved plan names"""
    plans = load_json(GAIT_FILE)
    return jsonify({"ok": True, "plans": list(plans.keys())})


@app.route("/api/gait/params/delete", methods=["POST"])
def api_gait_params_delete():
    """Delete a named gait plan"""
    data = request.json
    name = data.get("name", "").strip()
    plans = load_json(GAIT_FILE)
    if name in plans:
        del plans[name]
        save_json(GAIT_FILE, plans)
        return jsonify({"ok": True, "msg": f"Plan '{name}' deleted"})
    return jsonify({"ok": False, "error": "Plan not found"}), 404


@app.route("/api/gait/set-leg-pose", methods=["POST"])
def api_gait_set_leg_pose():
    """Apply a specific pose to a leg immediately (for testing)"""
    data = request.json
    group = data.get("group", "")
    pose = data.get("pose", {})
    if group not in LEG_GROUPS:
        return jsonify({"ok": False, "error": "invalid group"}), 400
    if not pose:
        return jsonify({"ok": False, "error": "pose required"}), 400
    leg_keys = LEG_GROUPS[group]
    smooth_move_leg(leg_keys, pose)
    return jsonify({"ok": True, "msg": f"Group {group} pose applied"})


@app.route("/api/gait/auto-start", methods=["POST"])
def api_gait_auto_start():
    """Start auto walking with a named plan"""
    global gait_running, gait_thread
    if gait_event.is_set():
        return jsonify({"ok": False, "error": "Already running"})
    data = request.json
    name = data.get("name", "")
    speed = float(data.get("speed", 1.0))
    max_steps = int(data.get("steps", 0))

    plans = load_json(GAIT_FILE)
    if name not in plans:
        return jsonify({"ok": False, "error": "Plan not found"})

    gait_running = True
    gait_event.set()
    gait_thread = threading.Thread(target=execute_gait, args=(plans[name]["params"], speed, max_steps), daemon=True)
    gait_thread.start()
    return jsonify({"ok": True, "msg": "Gait started"})


@app.route("/api/gait/auto-stop", methods=["POST"])
def api_gait_auto_stop():
    """Stop auto walking immediately"""
    global gait_running
    gait_running = False
    gait_event.clear()
    # Wait briefly for thread to finish current moves
    time.sleep(0.1)
    return jsonify({"ok": True, "msg": "Gait stopped"})


@app.route("/api/gait/status", methods=["GET"])
def api_gait_status():
    """Is gait running?"""
    return jsonify({
        "running": gait_running,
        "step_count": gait_step_count,
        "current_phase": gait_phase
    })


# ── Ripple Gait (مشي العنكبوت المتناغم) ──
RIPPLE_PARAMS_FILE = os.path.join(BASE_DIR, "ripple_gait_params.json")

def _load_ripple_params():
    """يحمّل بيانات Ripple Gait."""
    try:
        with open(RIPPLE_PARAMS_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _run_ripple_gait(params, speed=1.0, max_cycles=0, direction='forward'):
    """تنفيذ Ripple Gait — رجل واحدة بالهوا بالتسلسل."""
    global gait_running, gait_step_count, gait_current_phase

    RIPPLE_ORDER = params.get("RIPPLE_ORDER", ["RR", "RM", "RF", "LR", "LM", "LF"])
    LIFT_DEG  = params.get("LIFT_DEG", 14)
    SWING_DEG = params.get("SWING_DEG", 20)
    TOTAL_FRAMES = params.get("total_frames", 60)

    delay = (params.get("frame_delay_ms", 20) / 1000.0) / speed
    frame = 0
    cycles = 0

    # تحضير lookup لقنوات كل رجل
    legs_order = list(params["legs"].keys())

    while gait_event.is_set():
        if max_cycles > 0 and cycles >= max_cycles:
            break

        t_global = frame / TOTAL_FRAMES  # 0.0 → 1.0

        all_targets = {}
        for idx, leg_name in enumerate(RIPPLE_ORDER):
            leg = params["legs"][leg_name]
            side = leg["side"]

            # اتجاه الحركة
            if direction == 'strafe_left':
                sign = -1
            elif direction == 'strafe_right':
                sign = 1
            elif direction == 'turn_left':
                sign = 1 if side == "R" else -1
            elif direction == 'turn_right':
                sign = -1 if side == "R" else 1
            else:
                sign = 1 if side == "R" else -1
                if direction == 'backward':
                    sign = -sign

            # Phase مع إزاحة لكل رجل
            leg_phase = (t_global + idx / len(RIPPLE_ORDER)) % 1.0

            # ── Displacement-based gait ──
            # Duty factor: كل رجل عالأرض 5/6 من الوقت
            DUTY = 5.0 / 6.0
            SWING_FRAC = 1.0 - DUTY  # 1/6

            if leg_phase < SWING_FRAC:
                # === SWING (القدم بالهوا — تنتقل من الخلف للأمام) ===
                swing_t = leg_phase / SWING_FRAC  # 0→1
                swing_t_ease = ease_smoothstep(swing_t)

                # Coxa: من الخلف للأمام
                coxa_offset = (-SWING_DEG/2 + SWING_DEG * swing_t_ease) * sign
                # Femur: رفع القدم (قوس)
                lift = LIFT_DEG * math.sin(math.pi * swing_t)
            else:
                # === STANCE (القدم عالأرض — تدفع الجسم للأمام) ===
                stance_t = (leg_phase - SWING_FRAC) / DUTY  # 0→1
                stance_t_ease = ease_smoothstep(stance_t)

                # Coxa: من الأمام للخلف (دفع)
                coxa_offset = (SWING_DEG/2 - SWING_DEG * stance_t_ease) * sign
                # Femur: على الأرض
                lift = 0

            coxa  = leg["coxa_stand"]  + coxa_offset
            femur = leg["femur_stand"] + lift
            tibia = leg["tibia"]       - lift * 0.4

            # ── Balance correction — فقط للأرجل على الأرض ──
            if balance.enabled and lift < 0.5:
                femur += balance.femur_offsets.get(leg_name, 0.0)

            # حساب القنوات
            leg_idx = legs_order.index(leg_name)
            if side == "R":
                ch = leg_idx * 3
            else:
                ch = (leg_idx - 3) * 3

            all_targets[f"{side}{ch}"]   = max(45, min(135, round(coxa)))
            all_targets[f"{side}{ch+1}"] = max(45, min(135, round(femur)))
            all_targets[f"{side}{ch+2}"] = max(45, min(135, round(tibia)))

        gait_current_phase = f"ripple-{frame}"
        smooth_move_leg(list(all_targets.keys()), all_targets, steps=3, delay=0.01)

        frame += 1
        if frame >= TOTAL_FRAMES:
            frame = 0
            cycles += 1
            gait_step_count += 1

        time.sleep(delay)

    # إيقاف نظيف — ارجع لوضعية الوقوف
    gait_current_phase = "returning"
    stance_targets = {}
    for leg_name, leg_data in params["legs"].items():
        side = leg_data["side"]
        leg_idx = list(params["legs"].keys()).index(leg_name)
        if side == "R":
            ch = leg_idx * 3
        else:
            ch = (leg_idx - 3) * 3
        stance_targets[f"{side}{ch}"]   = leg_data["coxa_stand"]
        stance_targets[f"{side}{ch+1}"] = leg_data["femur_stand"]
        stance_targets[f"{side}{ch+2}"] = leg_data["tibia"]

    smooth_move_leg(list(stance_targets.keys()), stance_targets, steps=12, delay=0.03)
    gait_current_phase = "idle"
    gait_running = False
    gait_event.clear()


@app.route("/api/gait/ripple/start", methods=["POST"])
def ripple_gait_start():
    """تشغيل Ripple Gait — مشي العنكبوت المتناغم."""
    global gait_running, gait_thread, gait_step_count

    if gait_event.is_set():
        return jsonify({"ok": False, "error": "already running"})

    data = request.json or {}
    speed = float(data.get("speed", 1.0))
    max_cycles = int(data.get("cycles", 0))
    direction = data.get("direction", "forward")

    params = _load_ripple_params()
    if not params:
        return jsonify({"ok": False, "error": "ripple_gait_params.json not found"})

    gait_running = True
    gait_event.set()
    gait_step_count = 0
    gait_current_phase = "ripple_starting"

    gait_thread = threading.Thread(
        target=_run_ripple_gait,
        args=(params, speed, max_cycles, direction),
        daemon=True
    )
    gait_thread.start()
    return jsonify({"ok": True, "status": "ripple started", "speed": speed, "direction": direction})


@app.route("/api/gait/ripple/stop", methods=["POST"])
def ripple_gait_stop():
    """إيقاف Ripple Gait."""
    global gait_running
    gait_running = False
    gait_event.clear()
    return jsonify({"ok": True, "status": "ripple stopping..."})


@app.route("/api/gait/ripple/status", methods=["GET"])
def ripple_gait_status():
    """حالة Ripple Gait."""
    return jsonify({
        "running": gait_running,
        "step_count": gait_step_count,
        "current_phase": gait_current_phase
    })


# ── Forward Tripod Gait Endpoints ──
@app.route("/api/gait/forward/start", methods=["POST"])
def gait_forward_start():
    global gait_running, gait_thread, gait_step_count

    if gait_event.is_set():
        return jsonify({"ok": False, "error": "already running"})

    data = request.json or {}
    speed = float(data.get("speed", 1.0))
    max_cycles = int(data.get("cycles", 0))
    gait_type = data.get("type", "forward")

    params = _load_gait_params("forward_tripod")
    if not params:
        return jsonify({"ok": False, "error": "gait_params.json not found or invalid"})

    gait_running = True
    gait_event.set()
    gait_step_count = 0
    gait_current_phase = "starting"

    gait_thread = threading.Thread(
        target=_run_forward_gait,
        args=(params, speed, max_cycles, gait_type),
        daemon=True
    )
    gait_thread.start()
    return jsonify({"ok": True, "status": "forward started", "speed": speed, "type": gait_type})


@app.route("/api/gait/forward/stop", methods=["POST"])
def gait_forward_stop():
    global gait_running
    gait_running = False
    gait_event.clear()
    return jsonify({"ok": True, "status": "stopping..."})


@app.route("/api/gait/forward/status", methods=["GET"])
def gait_forward_status():
    return jsonify({
        "running": gait_running,
        "step_count": gait_step_count,
        "current_phase": gait_current_phase
    })


# ── Gait Once Endpoint (N cycles then stop) ──
@app.route("/api/gait/once", methods=["POST"])
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
    if gait_event.is_set():
        return jsonify({'ok': False, 'error': 'already running'})

    gait_running = True
    gait_event.set()
    gait_thread  = threading.Thread(
        target=_run_forward_gait,
        args=(params, speed, cycles, gait_type),
        daemon=True
    )
    gait_thread.start()
    return jsonify({'ok': True, 'type': gait_type, 'cycles': cycles})


# ── وضعية الوقوف الأساسية (من DEFAULT_RIGHT/LEFT) ──
_STAND = {}
for ch, angle in DEFAULT_RIGHT.items():
    _STAND[f"R{ch}"] = angle
for ch, angle in DEFAULT_LEFT.items():
    _STAND[f"L{ch}"] = angle


# ── Body Move Endpoint ──
@app.route("/api/body/move", methods=["POST"])
def body_move():
    """
    يحرّك الجسم بتغيير Femur أو Coxa لكل الأرجل معاً.
    لا يتضمن locomotion — الأرجل تبقى على الأرض.
    """
    data  = request.json or {}
    move  = data.get('move', 'stand')
    speed = float(data.get('speed', 1.0))

    base = dict(_STAND)

    BODY_DELTA = 12  # درجات التعديل

    moves = {
        # ارتفاع: كل Femur ترتفع قليلاً (قيمة أكبر = أعلى)
        'body_up':      {k: base[k] - BODY_DELTA if k[1] in ('1','4','7') else base[k]
                         for k in base},
        # انخفاض: كل Femur تنزل
        'body_down':    {k: base[k] + BODY_DELTA if k[1] in ('1','4','7') else base[k]
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
        # وقوف — الرجوع للوضعية الأساسية
        'stand':        dict(base),
    }

    if move not in moves:
        return jsonify({'ok': False, 'error': f'unknown move: {move}'})

    targets = {k: max(45, min(135, v)) for k, v in moves[move].items()}
    steps   = max(4, int(10 / speed))
    delay   = max(0.01, 0.03 / speed)
    smooth_move_leg(list(targets.keys()), targets, steps=steps, delay=delay)

    return jsonify({'ok': True, 'move': move})


# ── Special Move Functions ──────────────────────────────

def _move_wave(speed=1.0):
    """تلويح — RF ترتفع وتتأرجح يمين يسار 3 مرات."""
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
    """رقص — تأرجح الجسم يمين/يسار مع رفع أرجل متناوبة."""
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
    """مصافحة — RF تمتد للأمام وتهتز."""
    s  = max(4, int(8 / speed))
    dl = max(0.01, 0.02 / speed)
    smooth_move_leg(['R0','R1','R2'], {'R0':125,'R1':90,'R2':70}, steps=s, delay=dl)
    for _ in range(3):
        smooth_move_leg(['R1'], {'R1':100}, steps=3, delay=0.02)
        smooth_move_leg(['R1'], {'R1':80},  steps=3, delay=0.02)
    smooth_move_leg(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_salute(speed=1.0):
    """تحية — RF ترتفع وتلمس الجانب الأيمن للجسم."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    smooth_move_leg(['R0','R1','R2'], {'R0':60,'R1':125,'R2':130}, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    smooth_move_leg(['R0','R1','R2'], _STAND, steps=s, delay=dl)


def _move_roar(speed=1.0):
    """تهديد — كل الأرجل الأمامية ترتفع والجسم ينخفض."""
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
    """دوران كامل 360° — 8 دورات turn_right."""
    params = _load_gait_params('forward_tripod')
    if params:
        global gait_running
        gait_running = True
        gait_event.set()
        _run_forward_gait(params, speed * 1.2, 8, 'turn_right')


def _move_bow(speed=1.0):
    """انحناء — الأرجل الأمامية ترفع الجسم، الخلفية تنخفض."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    bow = {**_STAND,
           'R1': _STAND['R1'] + 20, 'L1': _STAND['L1'] + 20,   # أمام ترتفع
           'R7': _STAND['R7'] - 15, 'L7': _STAND['L7'] - 15}   # خلف تنزل
    smooth_move_leg(list(bow.keys()), bow, steps=s, delay=dl)
    time.sleep(0.6 / speed)
    smooth_move_leg(list(_STAND.keys()), _STAND, steps=s, delay=dl)


def _move_stretch(speed=1.0):
    """تمدد — كل الأرجل تمتد للخارج إلى أقصى مدى."""
    s  = max(5, int(10 / speed))
    dl = max(0.01, 0.03 / speed)
    stretch = {**_STAND,
               'R0':70,'R3':70,'R6':70,
               'L0':110,'L3':110,'L6':110,
               'R1':_STAND['R1']-10,'R4':_STAND['R4']-10,'R7':_STAND['R7']-10,
               'L1':_STAND['L1']-10,'L4':_STAND['L4']-10,'L7':_STAND['L7']-10}
    smooth_move_leg(list(stretch.keys()), stretch, steps=s, delay=dl)
    time.sleep(0.5 / speed)
    smooth_move_leg(list(_STAND.keys()), _STAND, steps=s, delay=dl)


def _move_idle_sway(speed=1.0):
    """تأرجح خفيف — حركة هادئة للاسترخاء (5 دورات)."""
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
    """إيقاظ — ينزل ببطء من وضعية نوم لوضعية وقوف."""
    s  = max(8, int(15 / speed))
    dl = max(0.02, 0.04 / speed)
    sleep_pos = {k: v + 40 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    smooth_move_leg(list(_STAND.keys()), sleep_pos, steps=2, delay=0.01)  # إلى النوم أولاً
    smooth_move_leg(list(_STAND.keys()), _STAND,    steps=s, delay=dl)    # ثم إيقاظ بطيء


def _move_sleep_pose(speed=1.0):
    """وضعية نوم — تنخفض الأرجل لينام الجسم على الأرض."""
    s  = max(8, int(15 / speed))
    dl = max(0.02, 0.04 / speed)
    sleep_pos = {k: v + 40 if k[1] in ('1','4','7') else v for k,v in _STAND.items()}
    smooth_move_leg(list(sleep_pos.keys()), sleep_pos, steps=s, delay=dl)


# ── Special Move Endpoint ──
@app.route("/api/special/<move_name>", methods=["POST"])
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


# ════════════════════════════════════════════════════════════════
# ── Smooth Ripple Gait (Foot Trajectory + Tibia ديناميكي) ──
# ════════════════════════════════════════════════════════════════

def foot_arc_trajectory(phase, stride_length, lift_height):
    """
    حساب مسار القدم — قوس D ناعم.
    phase: 0.0 → 1.0 (دورة كاملة)
    Returns: (coxa_offset, femur_lift)
    """
    if phase < 0.5:
        # ── Swing Phase (بالهوا) ──
        t = phase / 0.5  # 0→1
        t_smooth = ease_smoothstep(t)
        coxa_offset = -stride_length / 2 + stride_length * t_smooth
        femur_lift = lift_height * math.sin(t * math.pi)
    else:
        # ── Stance Phase (على الأرض) ──
        t = (phase - 0.5) / 0.5  # 0→1
        t_smooth = ease_smoothstep(t)
        coxa_offset = stride_length / 2 - stride_length * t_smooth
        femur_lift = 0.0
    return coxa_offset, femur_lift


def tibia_compensation(lift_ratio, max_comp=0.5):
    """
    تعويض Tibia — يعاكس Femur عشان القدم تبقى أفقية نسبياً.
    lift_ratio: 0.0 (على الأرض) → 1.0 (أعلى نقطة)
    """
    comp = ease_smoothstep(lift_ratio) * max_comp
    return comp


# اتجاهات Coxa و Tibia
COXA_DIRECTION = {
    "RF": +1, "RM": +1, "RR": +1,
    "LF": -1, "LM": -1, "LR": -1,
}
TIBIA_DIRECTION = {
    "RF": +1, "RM": +1, "RR": +1,
    "LF": -1, "LM": -1, "LR": -1,
}


def _run_smooth_ripple(params, speed=1.0, max_cycles=0, direction='forward'):
    """Ripple Gait محسّن مع Foot Trajectory + Tibia ديناميكي + Balance"""
    global gait_running, gait_step_count, gait_current_phase

    RIPPLE_ORDER = params.get("RIPPLE_ORDER", ["RR", "RM", "RF", "LR", "LM", "LF"])
    LIFT_DEG = params.get("LIFT_DEG", 14)
    SWING_DEG = params.get("SWING_DEG", 20)
    TOTAL_FRAMES = params.get("total_frames", 60)

    delay = (params.get("frame_delay_ms", 20) / 1000.0) / speed
    frame = 0
    cycles = 0
    phase_offset = 1.0 / len(RIPPLE_ORDER)

    legs_order = list(params["legs"].keys())

    while gait_event.is_set():
        if max_cycles > 0 and cycles >= max_cycles:
            break

        global_phase = frame / TOTAL_FRAMES
        all_targets = {}

        for idx, leg_name in enumerate(RIPPLE_ORDER):
            leg = params["legs"][leg_name]
            side = leg["side"]

            # حساب phase لهاي الرجل (مع offset)
            leg_phase = (global_phase + idx * phase_offset) % 1.0

            # اتجاه الحركة
            if direction == 'strafe_left':
                sign = -1
            elif direction == 'strafe_right':
                sign = 1
            elif direction == 'turn_left':
                sign = 1 if side == "R" else 1
            elif direction == 'turn_right':
                sign = -1 if side == "R" else -1
            else:
                sign = 1 if side == "R" else -1
                if direction == 'backward':
                    sign = -sign

            # ── Foot Trajectory (قوس D) ──
            stride = SWING_DEG * abs(sign) if sign != 0 else SWING_DEG
            coxa_off, femur_lift = foot_arc_trajectory(leg_phase, stride, LIFT_DEG)

            # Tibia تعويض ديناميكي
            lift_ratio = femur_lift / LIFT_DEG if LIFT_DEG > 0 else 0
            tibia_off = -femur_lift * tibia_compensation(lift_ratio, max_comp=0.5)

            # ── Balance correction — فقط إذا على الأرض ──
            balance_off = 0.0
            if balance.enabled and femur_lift < 0.5:
                balance_off = balance.femur_offsets.get(leg_name, 0.0)

            # حساب الزوايا النهائية
            coxa_target = leg["coxa_stand"] + coxa_off * COXA_DIRECTION[leg_name] * sign
            femur_target = leg["femur_stand"] + femur_lift * FEMUR_DIRECTION[leg_name] * (-1) + balance_off
            tibia_target = leg["tibia"] + tibia_off * TIBIA_DIRECTION[leg_name]

            # حدود أمان
            coxa_target = max(50, min(130, round(coxa_target)))
            femur_target = max(50, min(130, round(femur_target)))
            tibia_target = max(50, min(130, round(tibia_target)))

            # حساب القنوات
            leg_idx = legs_order.index(leg_name)
            if side == "R":
                ch = leg_idx * 3
            else:
                ch = (leg_idx - 3) * 3

            all_targets[f"{side}{ch}"] = coxa_target
            all_targets[f"{side}{ch+1}"] = femur_target
            all_targets[f"{side}{ch+2}"] = tibia_target

        gait_current_phase = f"smooth-ripple-{frame}"
        smooth_move_leg(list(all_targets.keys()), all_targets, steps=3, delay=0.01)

        frame += 1
        if frame >= TOTAL_FRAMES:
            frame = 0
            cycles += 1
            gait_step_count += 1

        remaining = delay - 3 * 0.01
        time.sleep(max(0, remaining))

    # إيقاف نظيف — ارجع لوضعية الوقوف
    gait_current_phase = "returning"
    stance_targets = {}
    for leg_name, leg_data in params["legs"].items():
        side = leg_data["side"]
        leg_idx = list(params["legs"].keys()).index(leg_name)
        if side == "R":
            ch = leg_idx * 3
        else:
            ch = (leg_idx - 3) * 3
        stance_targets[f"{side}{ch}"] = leg_data["coxa_stand"]
        stance_targets[f"{side}{ch+1}"] = leg_data["femur_stand"]
        stance_targets[f"{side}{ch+2}"] = leg_data["tibia"]

    smooth_move_leg(list(stance_targets.keys()), stance_targets, steps=12, delay=0.03)
    gait_current_phase = "idle"
    gait_running = False
    gait_event.clear()


@app.route('/api/gait/smooth-ripple/start', methods=['POST'])
def smooth_ripple_start():
    """تشغيل Smooth Ripple Gait — Foot Trajectory + Tibia ديناميكي."""
    global gait_running, gait_thread, gait_step_count

    if gait_event.is_set():
        return jsonify({"ok": False, "error": "already running"})

    data = request.json or {}
    speed = float(data.get("speed", 1.0))
    max_cycles = int(data.get("cycles", 0))
    direction = data.get("direction", "forward")

    params = _load_ripple_params()
    if not params:
        return jsonify({"ok": False, "error": "ripple_gait_params.json not found"})

    gait_running = True
    gait_event.set()
    gait_step_count = 0
    gait_current_phase = "smooth_ripple_starting"

    gait_thread = threading.Thread(
        target=_run_smooth_ripple,
        args=(params, speed, max_cycles, direction),
        daemon=True
    )
    gait_thread.start()
    return jsonify({"ok": True, "status": "smooth ripple started", "speed": speed, "direction": direction})


@app.route('/api/gait/smooth-ripple/stop', methods=['POST'])
def smooth_ripple_stop():
    """إيقاف Smooth Ripple Gait."""
    global gait_running
    gait_running = False
    gait_event.clear()
    return jsonify({"ok": True, "status": "smooth ripple stopping..."})


# ── startup IMU zero ──
def _startup_imu_zero():
    """يصفّر الـ IMU عند بداية التشغيل"""
    if not BNO_READY:
        return
    print("Zeroing IMU...")
    rolls, pitches = [], []
    for _ in range(20):
        r = read_bno_single()  # تستخدم bno_lock داخلياً — ما نستخدم with هنا
        if r:
            rolls.append(r["raw_roll"])
            pitches.append(r["raw_pitch"])
        time.sleep(0.06)
    if rolls:
        global bno_zero_roll, bno_zero_pitch
        bno_zero_roll = sum(rolls) / len(rolls)
        bno_zero_pitch = sum(pitches) / len(pitches)
        print(f"IMU zeroed: roll={bno_zero_roll:.2f} pitch={bno_zero_pitch:.2f}")


if __name__ == "__main__":
    print(f"Hardware: {'CONNECTED' if HARDWARE else 'SIMULATION MODE'}")
    print(f"IMU (BNO085): {'READY' if BNO_READY else 'NOT FOUND'}")
    _startup_imu_zero()
    print("Spider Web Controller → http://0.0.0.0:5000")
    print("⚡ المحركات نايمة — أول أمر حركة يفعّلها")
    app.run(host="0.0.0.0", port=5000, debug=False)
