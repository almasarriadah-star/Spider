from flask import Flask, render_template, request, jsonify
import json
import math
import os
import time
import threading

app = Flask(__name__)

# ── Hardware Setup ──
from spider.hardware import HARDWARE, right_pca, left_pca, i2c_lock

# ── Constants (من spider.constants) ──
from spider.constants import (
    MIN_ANGLE, MAX_ANGLE,
    DEFAULT_RIGHT, DEFAULT_LEFT,
    SERVO_NAMES, LEG_GROUPS, _STAND,
)

# ── IMU (من spider.imu) ──
from spider.imu import (
    BNO_READY, read_bno_single,
    bno_zero_roll, bno_zero_pitch,
    startup_imu_zero, imu_zero_manual, imu_stream,
)
import spider.imu as _imu_mod   # مرجع مباشر لتحديث bno_zero_*

BASE_DIR = os.path.dirname(__file__)
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
DEFAULTS_FILE  = os.path.join(BASE_DIR, "leg_defaults.json")
PRESETS_FILE   = os.path.join(BASE_DIR, "leg_presets.json")

# ── Gait State ──
GAIT_FILE          = os.path.join(BASE_DIR, "config", "gait_params.json")
BALANCE_CONFIG_FILE = os.path.join(BASE_DIR, "config", "balance_config.json")
gait_running      = False
gait_event        = threading.Event()
gait_thread       = None
gait_lock         = threading.Lock()
gait_step_count   = 0
gait_phase        = "A"
gait_current_phase = "idle"

# ── State ──
import spider
current = spider.current
from spider.safety import arbiter, set_force_stop

_servos_initialized = False


def limit_angle(a):
    return max(MIN_ANGLE, min(MAX_ANGLE, int(a)))


def set_servo(key, angle):
    owner = arbiter.owner()
    arbiter.set_target(owner, {key: angle})
    from spider.config import servo_limit
    return servo_limit(key, angle)


def set_servos_batch(updates):
    """يكتب عدة محركات دفعة واحدة عبر محكّم الحركة الآمن"""
    owner = arbiter.owner()
    arbiter.set_target(owner, updates)


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


def apply_startup_calibration():
    """يحرّك المحركات لوضعية التوازن — ينطبق فقط أول مرة يُطلب فيها"""
    global _servos_initialized
    if _servos_initialized:
        return
    try:
        arbiter.start()
    except Exception as e:
        print(f"Error starting arbiter: {e}")

    defaults = load_leg_defaults()
    print("Applying startup calibration using MotionArbiter...")

    targets = {}
    for group, keys in LEG_GROUPS.items():
        for key in keys:
            base = DEFAULT_RIGHT[int(key[1:])] if key[0] == "R" else DEFAULT_LEFT[int(key[1:])]
            targets[key] = defaults.get(group, {}).get(key, base)

    if arbiter.acquire("startup"):
        from spider import goto
        goto("startup", targets, timeout=8.0)
        arbiter.release("startup")

    _servos_initialized = True
    print("Startup calibration applied - servos holding position")


def _ensure_servos_on():
    """تأكد إن المحركات شغّالة — أول command يفعّلها"""
    if not _servos_initialized:
        apply_startup_calibration()


# ── Gait Helpers ──
_force_stop = False

# ── Easing & Gait Functions (الاستيراد من spider.gaits) ──
from spider.gaits import (
    ease_smoothstep, ease_cosine, smooth_move_leg, set_ease, get_ease_name,
    _run_forward_gait, _run_lateral_gait, _run_ripple_gait, _run_smooth_ripple,
    _load_gait_params, _load_ripple_params, get_leg_angles
)
_ease_func = ease_smoothstep

class GlobalRef:
    def __init__(self, name):
        self.name = name

    def __getitem__(self, item):
        return globals()[self.name]

    def __setitem__(self, item, value):
        globals()[self.name] = value


def _sync_force_stop(val: bool):
    """يضبط _force_stop محلياً وفي spider.safety معاً."""
    global _force_stop
    _force_stop = val
    set_force_stop(val)


# ── حماية سباق ملكية المحكّم (إصلاح BUG03) ──
def _gait_busy():
    """True لو فيه خيط مشي ما زال حياً أو الحدث مضبوط.
    يفحص is_alive() وليس الحدث فقط — لأن الخيط يبقى يملك "gait" أثناء
    رجوعه لوضعية الوقوف بعد مسح الحدث."""
    return gait_event.is_set() or (gait_thread is not None and gait_thread.is_alive())


def _stop_gait_sync(timeout=2.0):
    """إيقاف متزامن: يمسح الحدث وينتظر الخيط حتى يحرّر الملكية فعلاً.
    هذا يمنع سباق إعادة التشغيل (بدء خيط ثانٍ قبل أن ينتهي الأول)."""
    global gait_running
    gait_running = False
    gait_event.clear()
    t = gait_thread
    if t is not None and t.is_alive():
        t.join(timeout=timeout)





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

    while gait_event.is_set() and not arbiter.in_estop() and (max_steps == 0 or gait_step_count < max_steps):
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





# ════════════════════════════════════════════════════════════════
# ── Balance System (من spider.balance) ──
# ════════════════════════════════════════════════════════════════
from spider.balance import BalanceController, SimplePID, BALANCE_WEIGHTS, LEG_FEMUR_CHANNEL


# ── إنشاء الـ balance controller (مع حقن dependencies) ──
balance = BalanceController()
balance.configure(
    set_servo_fn=lambda ch, angle: set_servo(ch, angle),
    current_ref=current,
    gait_event_ref=gait_event,
)


# ── Routes ──
@app.route("/home")
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
        "hardware": HARDWARE,
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
    """إطفاء نظيف: وقوف على _STAND (مفاتيح صحيحة) ثم تصفير PWM."""
    global gait_running
    _sync_force_stop(True)
    gait_running = False
    gait_event.clear()
    time.sleep(0.1)
    _sync_force_stop(False)

    if HARDWARE:
        try:
            # ارجع لوضعية الوقوف الصحيحة عبر _STAND (R0..R8, L0..L8 — مفاتيح صحيحة)
            smooth_move_leg(list(_STAND.keys()), _STAND, steps=8, delay=0.03)
            time.sleep(0.1)
        except Exception:
            pass
        try:
            # صفّر PWM — ترخية السيرفوات
            from spider.hardware import pwm_release_all
            pwm_release_all()
        except Exception:
            pass
    return jsonify({"ok": True, "msg": "Stance then servos off"})


# ── الإيقاف الطارئ البرمجي ──
@app.route("/api/estop", methods=["POST"])
def api_estop():
    """إيقاف طارئ فوري: يقطع PWM أولاً (زمن استجابة ثابت) ثم يوحّد العلم ويوقف الباقي."""
    global gait_running
    arbiter.emergency_stop()    # 1) أولاً: قطع التغذية/PWM فوراً — لا تأخير قبله
    _sync_force_stop(True)      # 2) وحّد علم الإيقاف في web + spider معاً
    gait_running = False
    gait_event.clear()          # 3) أوقف حلقات المشي
    try:
        balance.stop()          # 4) أخيراً: أوقف التوازن (قد يحجب حتى ثانيتين)
    except Exception:
        pass
    return jsonify({"ok": True, "estop": True})


@app.route("/api/estop/clear", methods=["POST"])
def api_estop_clear():
    """إلغاء الطوارئ: يعيد المحكّم للعمل بهدف=الوضع الحالي (لا حركة مفاجئة)."""
    _sync_force_stop(False)     # وحّد العلم في web + spider (بدل _force_stop = False)
    arbiter.clear_estop()
    return jsonify({"ok": True, "estop": False})


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





@app.route("/api/gait/ripple/start", methods=["POST"])
def ripple_gait_start():
    """تشغيل Ripple Gait — مشي العنكبوت المتناغم."""
    global gait_running, gait_thread, gait_step_count

    if arbiter.in_estop():
        return jsonify({"ok": False, "error": "الطوارئ مفعّل — اضغط «إلغاء الطوارئ» أولاً"}), 409
    if _gait_busy():
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

    if direction in ('strafe_left', 'strafe_right'):
        lat_dir = 'left' if direction == 'strafe_left' else 'right'
        tripod_params = _load_gait_params("forward_tripod")
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(tripod_params or params, speed, max_cycles, lat_dir),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_ripple_gait,
            args=(params, speed, max_cycles, direction),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    gait_thread.start()
    return jsonify({"ok": True, "status": "ripple started", "speed": speed, "direction": direction})


@app.route("/api/gait/ripple/stop", methods=["POST"])
def ripple_gait_stop():
    """إيقاف Ripple Gait (متزامن)."""
    _stop_gait_sync()
    return jsonify({"ok": True, "status": "ripple stopped"})


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

    if arbiter.in_estop():
        return jsonify({"ok": False, "error": "الطوارئ مفعّل — اضغط «إلغاء الطوارئ» أولاً"}), 409
    if _gait_busy():
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

    lateral_types = {
        'shift_left': 'left', 'shift_right': 'right',
        'strafe_left': 'left', 'strafe_right': 'right',
        'crab_walk': 'left',
    }

    if gait_type in lateral_types:
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(params, speed, max_cycles, lateral_types[gait_type]),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_forward_gait,
            args=(params, speed, max_cycles, gait_type),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    gait_thread.start()
    return jsonify({"ok": True, "status": "forward started", "speed": speed, "type": gait_type})


@app.route("/api/gait/forward/stop", methods=["POST"])
def gait_forward_stop():
    _stop_gait_sync()
    return jsonify({"ok": True, "status": "stopped"})


@app.route("/api/gait/forward/status", methods=["GET"])
def gait_forward_status():
    return jsonify({
        "running": gait_running,
        "step_count": gait_step_count,
        "current_phase": gait_current_phase,
        "estop": arbiter.in_estop()
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
    if arbiter.in_estop():
        return jsonify({'ok': False, 'error': 'الطوارئ مفعّل — اضغط «إلغاء الطوارئ» أولاً'}), 409
    if _gait_busy():
        return jsonify({'ok': False, 'error': 'already running'})

    gait_running = True
    gait_event.set()

    lateral_types = {
        'shift_left': 'left', 'shift_right': 'right',
        'strafe_left': 'left', 'strafe_right': 'right',
        'crab_walk': 'left',
    }

    if gait_type in lateral_types:
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(params, speed, cycles, lateral_types[gait_type]),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_forward_gait,
            args=(params, speed, cycles, gait_type),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
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


# ── Body Move Endpoint (محمي بالتحكيم — ثغرة #1) ──
@app.route("/api/body/move", methods=["POST"])
def body_move():
    """يحرّك الجسم بتغيير Femur أو Coxa — الأرجل تبقى على الأرض."""
    from spider.moves import get_body_targets, run_body
    data  = request.json or {}
    move  = data.get('move', 'stand')
    speed = float(data.get('speed', 1.0))

    targets_raw = get_body_targets(move)
    if targets_raw is None:
        return jsonify({'ok': False, 'error': f'unknown move: {move}'})

    targets = {k: max(45, min(135, v)) for k, v in targets_raw.items()}
    steps   = max(4, int(10 / speed))
    delay   = max(0.01, 0.03 / speed)

    if not run_body(move, targets, steps, delay):
        return jsonify({'ok': False, 'error': 'busy — أوقف المشي أولاً'}), 409
    return jsonify({'ok': True, 'move': move})


# ── Special Move Functions ──────────────────────────────




# ── Special Move Endpoint (محمي بالتحكيم — ثغرة #1) ──
@app.route("/api/special/<move_name>", methods=["POST"])
def special_move(move_name):
    from spider.moves import SPECIALS, run_special
    data  = request.json or {}
    speed = float(data.get('speed', 1.0))

    fn = SPECIALS.get(move_name)
    if not fn:
        return jsonify({'ok': False, 'error': f'unknown special: {move_name}'}), 404

    if not run_special(move_name, fn, speed):
        return jsonify({'ok': False, 'error': 'busy — أوقف المشي أولاً'}), 409
    return jsonify({'ok': True, 'move': move_name})


# ════════════════════════════════════════════════════════════════



@app.route('/api/gait/smooth-ripple/start', methods=['POST'])
def smooth_ripple_start():
    """تشغيل Smooth Ripple Gait — Foot Trajectory + Tibia ديناميكي."""
    global gait_running, gait_thread, gait_step_count

    if arbiter.in_estop():
        return jsonify({"ok": False, "error": "الطوارئ مفعّل — اضغط «إلغاء الطوارئ» أولاً"}), 409
    if _gait_busy():
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

    if direction in ('strafe_left', 'strafe_right'):
        lat_dir = 'left' if direction == 'strafe_left' else 'right'
        tripod_params = _load_gait_params("forward_tripod")
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(tripod_params or params, speed, max_cycles, lat_dir),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_smooth_ripple,
            args=(params, speed, max_cycles, direction),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            },
            daemon=True
        )
    gait_thread.start()
    return jsonify({"ok": True, "status": "smooth ripple started", "speed": speed, "direction": direction})


@app.route('/api/gait/smooth-ripple/stop', methods=['POST'])
def smooth_ripple_stop():
    """إيقاف Smooth Ripple Gait (متزامن)."""
    _stop_gait_sync()
    return jsonify({"ok": True, "status": "smooth ripple stopped"})

# ── Live Tuning and Config Management (Plan 04) ──
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

def _cfg_path(name):
    allowed = ["servo_limits", "gait_params", "ripple_gait_params", "balance_config", "motion"]
    if name not in allowed:
        return None
    return os.path.join(CONFIG_DIR, name + ".json")

def _reload_config(name):
    """إعادة تحميل حيّ بلا إعادة تشغيل السيرفر."""
    if name == "servo_limits":
        from spider import config as _c
        _c.reload()
    elif name == "balance_config":
        balance.reload()
    elif name == "motion":
        from spider import safety, gaits, moves
        safety.reload_motion()
        gaits.reload_motion()
        moves.reload_motion()
    # gait_params/ripple تُقرأ عند بدء كل مشي، فلا تحتاج reload خاص

@app.route("/api/config/<name>", methods=["GET"])
def config_get(name):
    p = _cfg_path(name)
    if not p or not os.path.exists(p):
        return jsonify({"ok": False, "error": f"Config {name} not found"}), 404
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/config/<name>", methods=["POST"])
def config_set(name):
    import shutil
    p = _cfg_path(name)
    if not p or not os.path.exists(p):
        return jsonify({"ok": False, "error": f"Config {name} not found"}), 404
    try:
        # نسخة احتياطية قبل الكتابة
        shutil.copy(p, p + ".bak")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(request.json, f, indent=2, ensure_ascii=False)
        _reload_config(name)
        return jsonify({"ok": True, "saved": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/config/<name>/restore", methods=["POST"])
def config_restore(name):
    import shutil
    p = _cfg_path(name)
    if not p:
        return jsonify({"ok": False, "error": "Invalid config name"}), 400
    bak = p + ".bak"
    if os.path.exists(bak):
        try:
            shutil.copy(bak, p)
            _reload_config(name)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": False, "error": "no backup found"}), 404

@app.route("/api/limit/nudge", methods=["POST"])
def limit_nudge():
    """حرّك محركاً واحداً لزاوية (للمعايرة بالملاحظة). يتجاوز حدوده مؤقتاً بحذر."""
    d = request.json
    key = d.get("key")
    angle = d.get("angle")
    if key is None or angle is None:
        return jsonify({"ok": False, "error": "Missing key or angle"}), 400
    
    # حماية التحكيم
    if not arbiter.acquire("limit_tune"):
        return jsonify({"ok": False, "error": "Arbiter busy"}), 409
        
    try:
        angle = int(angle)
        # كتابة مباشرة للمحرك دون حواجز الحدود لأننا بصدد المعايرة
        from spider.hardware import write_batch_raw
        write_batch_raw({key: angle})
        # وتحديث current لتسجيل الحالة الحالية
        from spider.safety import current
        current[key] = angle
        return jsonify({"ok": True, "key": key, "angle": angle})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        arbiter.release("limit_tune")

@app.route("/api/limit/set", methods=["POST"])
def limit_set():
    """يحفظ حدّاً آمناً لمحرك في servo_limits.json."""
    d = request.json
    key = d.get("key")
    mn = d.get("min")
    mx = d.get("max")
    if key is None or mn is None or mx is None:
        return jsonify({"ok": False, "error": "Missing parameters"}), 400
        
    p = _cfg_path("servo_limits")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "overrides" not in data:
            data["overrides"] = {}
        data["overrides"][key] = {"min": int(mn), "max": int(mx)}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        from spider import config as _c
        _c.reload()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


# ════════════════════════════════════════════════════════════════
# ── خطة 05: مسار /api/move الموحّد (للجوي ستيك) ──
# ════════════════════════════════════════════════════════════════
import math as _math

_move_active_type = [None]    # نوع المشي الحالي الصادر من الجوي ستيك


@app.route("/api/move", methods=["POST"])
def api_move():
    """مسار الجوي ستيك الموحّد: يستقبل (vx,vy,omega) ويبدأ/يوقف المشي تلقائياً."""
    global gait_running, gait_thread, gait_step_count
    d = request.json or {}
    vx = float(d.get("vx", 0))
    vy = float(d.get("vy", 0))
    om = float(d.get("omega", 0))

    # حدّث أمر الحركة لمحاكاة GPS (آمن دائماً)
    try:
        from spider.sensors.gps import gps as _g
        _g.set_motion(vx, vy, om)
    except Exception:
        pass

    # شرط الإيقاف
    if abs(vx) < 0.05 and abs(vy) < 0.05 and abs(om) < 0.05:
        if _gait_busy():
            _stop_gait_sync(timeout=2.0)
        _move_active_type[0] = None
        return jsonify({"ok": True, "stopped": True})

    if arbiter.in_estop():
        return jsonify({"ok": False, "error": "estop active"}), 409

    # حدّد نوع الحركة + السرعة
    mag = _math.hypot(vx, vy)
    speed = max(0.3, min(2.0, max(mag, abs(om))))

    # نمط المشي الأمامي المختار من الواجهة (forward/glide/climb/prowl/high_step/creep)
    style = d.get("gait", "forward")
    forward_styles = {"forward", "glide", "climb", "prowl", "high_step", "creep"}

    if abs(om) > 0.3 and abs(om) >= mag:
        # المحرّك يدعم turn_left/turn_right (وليس rotate_cw/ccw)
        gait_type = "turn_right" if om > 0 else "turn_left"
    elif abs(vy) >= abs(vx):
        # اصطلاح: vy>0 = يسار (يطابق الجوي ستيك: سحب لليسار → vy موجب)
        gait_type = "shift_left" if vy > 0 else "shift_right"
    elif vx > 0:
        gait_type = style if style in forward_styles else "forward"
    else:
        gait_type = "backward"

    # نفس الحركة شغّالة → استمر بلا إعادة تشغيل
    if _gait_busy() and _move_active_type[0] == gait_type:
        return jsonify({"ok": True, "running": gait_type, "speed": speed})

    # حركة مختلفة أو غير شغّالة → أوقف أولاً ثم ابدأ
    if _gait_busy():
        _stop_gait_sync(timeout=1.5)

    params = _load_gait_params("forward_tripod")
    if not params:
        return jsonify({"ok": False, "error": "params not found"})

    _move_active_type[0] = gait_type
    gait_running = True
    gait_event.set()
    gait_step_count = 0

    lateral_map = {"shift_left": "left", "shift_right": "right"}
    if gait_type in lateral_map:
        gait_thread = threading.Thread(
            target=_run_lateral_gait,
            args=(params, speed, 0, lateral_map[gait_type]),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            }, daemon=True
        )
    else:
        gait_thread = threading.Thread(
            target=_run_forward_gait,
            args=(params, speed, 0, gait_type),
            kwargs={
                "gait_event": gait_event,
                "gait_running_ref": GlobalRef("gait_running"),
                "balance": balance,
                "step_counter_ref": GlobalRef("gait_step_count"),
                "phase_ref": GlobalRef("gait_current_phase")
            }, daemon=True
        )
    gait_thread.start()
    return jsonify({"ok": True, "started": gait_type, "speed": speed})


# ════════════════════════════════════════════════════════════════
# ── خطة 06: الكاميرات (RGB + حرارية) ──
# ════════════════════════════════════════════════════════════════
from flask import Response
from spider.sensors.camera import RGBCamera, ThermalCamera
from spider.hardware import i2c_lock as _i2c_lock
from spider.config import THERMAL_PORT as _THERMAL_PORT, THERMAL_BAUD as _THERMAL_BAUD
from spider import config as _cfg_mod

_rgb_cam = RGBCamera()
# موديول MLX90640 بخرج UART على UART5 — إعدادات فك الإطار من config/sensors.json.
# رجوع تلقائي بلا عتاد (مثلاً على ويندوز).
_th_cfg = _cfg_mod.SENSORS["thermal"]
_thermal_cam = ThermalCamera(
    i2c_lock=_i2c_lock, port=_THERMAL_PORT, baud=_THERMAL_BAUD,
    rows=_th_cfg.get("rows", 24), cols=_th_cfg.get("cols", 32),
    header=_th_cfg.get("header", "5A5A0206"),
    encoding=_th_cfg.get("encoding", "i16"),
    scale=_th_cfg.get("scale", 100.0),
    init=_th_cfg.get("init"))


@app.route("/video/rgb")
def video_rgb():
    return Response(_rgb_cam.mjpeg(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/video/thermal")
def video_thermal():
    return Response(_thermal_cam.mjpeg(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/thermal/frame")
def thermal_frame():
    m = _thermal_cam.read_matrix()
    if m is None:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "shape": list(m.shape),
                    "min": float(m.min()), "max": float(m.max()),
                    "data": [[round(v, 1) for v in row] for row in m.tolist()]})


@app.route("/api/cameras/status")
def cameras_status():
    return jsonify({
        "rgb": _rgb_cam.backend is not None,
        "thermal": _thermal_cam.ready
    })


# ════════════════════════════════════════════════════════════════
# ── خطة 07: GPS + مخزن التتبّع + SSE ──
# ════════════════════════════════════════════════════════════════
from spider.sensors.gps import gps as _gps
from spider.telemetry import store as _track_store


def _gps_feeder():
    """خيط يضيف قراءات GPS للمسار كل ثانية."""
    import time as _t
    while True:
        d = _gps.data
        if d.get("fix"):
            _track_store.add_fix(d["lat"], d["lon"], d.get("ts"))
        _t.sleep(1.0)


_gps.start()
threading.Thread(target=_gps_feeder, name="GPSFeeder", daemon=True).start()


@app.route("/api/gps/now")
def gps_now():
    return jsonify(_gps.data)


@app.route("/api/gps/track")
def gps_track():
    return jsonify(_track_store.snapshot())


@app.route("/api/gps/track/clear", methods=["POST"])
def gps_track_clear():
    d = request.json or {}
    _track_store.clear_pois(d.get("type"))
    return jsonify({"ok": True})


@app.route("/api/stream")
def sensor_stream():
    """SSE: يبثّ موقع GPS + حالة المشي كل ثانية."""
    import time as _t
    import json as _json

    def gen():
        while True:
            payload = {
                "gps": _gps.data,
                "gait": {
                    "running": gait_running,
                    "phase": gait_current_phase,
                    "estop": arbiter.in_estop()
                }
            }
            yield f"data: {_json.dumps(payload)}\n\n"
            _t.sleep(1.0)

    return Response(gen(), mimetype="text/event-stream")


@app.route("/map")
def map_page():
    return render_template("map.html")


# ════════════════════════════════════════════════════════════════
# ── خطة 08: الليدار ──
# ════════════════════════════════════════════════════════════════
from spider.sensors.lidar import lidar as _lidar, obstacles_world as _obstacles_world

_lidar.start()


@app.route("/api/lidar/scan")
def lidar_scan():
    return jsonify({
        "ready": _lidar.ready,
        "scan": _lidar.get_scan(),
        "distance_mm": _lidar.distance_mm,
        "strength": _lidar.strength,
        "simulate": _lidar.simulate,
    })


@app.route("/api/lidar/status")
def lidar_status():
    sc = _lidar.get_scan()
    return jsonify({
        "ready": _lidar.ready,
        "points": len(sc),
        "distance_mm": _lidar.distance_mm,
        "simulate": _lidar.simulate,
    })


@app.route("/api/lidar/project", methods=["POST"])
def lidar_project():
    """يُسقط العوائق الحالية على الخريطة كـ POI عالمية."""
    g = _gps.data
    heading = 0.0
    try:
        r = read_bno_single()
        if r:
            heading = r.get("yaw", 0.0)
    except Exception:
        pass
    if not g.get("fix"):
        return jsonify({"ok": False, "error": "no gps fix"})
    pts = _obstacles_world(_lidar.get_scan(), g["lat"], g["lon"], heading)
    for p in pts:
        _track_store.add_poi("lidar_obstacle", p["lat"], p["lon"], {"dist": p["dist"]})
    return jsonify({"ok": True, "added": len(pts)})


# ════════════════════════════════════════════════════════════════
# ── خطة 10: غاز MQ135 + DHT22 + سيرفوات مساعدة ──
# ════════════════════════════════════════════════════════════════
from spider.sensors.digital import gas as _gas, dht22 as _dht22
from spider.sensors import aux_servo as _aux


@app.route("/api/environment")
def environment_read():
    """إنذار الغاز (MQ135) + حرارة/رطوبة الجو (DHT22)."""
    air = _dht22.read()
    return jsonify({
        "gas_alarm": _gas.alarm(), "gas_sim": _gas.simulate,
        "air_temp": air["temp"], "air_humidity": air["humidity"],
        "dht_sim": _dht22.simulate,
    })


@app.route("/api/aux_servo", methods=["POST"])
def aux_servo_set():
    """يضبط زاوية سيرفو مساعد: which=camera|soil, angle=0..180."""
    d = request.json or {}
    ok, res = _aux.set_by_name(d.get("which"), d.get("angle", 90))
    if not ok:
        return jsonify({"ok": False, "error": res})
    return jsonify({"ok": True, "angle": res, "state": _aux.get_state()})


@app.route("/api/aux_servo")
def aux_servo_state():
    return jsonify({"ok": True, "state": _aux.get_state()})


# ════════════════════════════════════════════════════════════════
# ── خطة 09: رطوبة التربة + كشف الأعشاب + مسح آلي ──
# ════════════════════════════════════════════════════════════════
from spider.sensors.soil import soil as _soil
from spider.vision.weeds import analyze_frame as _analyze_frame, classify_weed as _classify_weed

_soil.start()   # رطوبة التربة صارت USB‑Serial — تحتاج خيط قراءة

import time as _time_mod

_survey = {"on": False, "min_dist_m": 1.0, "last": None}


def _haversine(a, b):
    R = 6371000
    la1, lo1, la2, lo2 = (_math.radians(x) for x in [a[0], a[1], b[0], b[1]])
    h = (_math.sin((la2 - la1) / 2) ** 2 +
         _math.cos(la1) * _math.cos(la2) * _math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R * _math.asin(_math.sqrt(h))


def _survey_loop():
    while True:
        if _survey["on"]:
            g = _gps.data
            if g.get("fix"):
                last = _survey["last"]
                moved = 999 if last is None else _haversine(last, (g["lat"], g["lon"]))
                if moved >= _survey["min_dist_m"]:
                    _survey["last"] = (g["lat"], g["lon"])
                    _track_store.add_poi("soil", g["lat"], g["lon"],
                                         {"moisture": _soil.read_percent()})
                    try:
                        f = _rgb_cam.read()
                        if f is not None:
                            _track_store.add_poi("weed", g["lat"], g["lon"],
                                                  _analyze_frame(f))
                    except Exception:
                        pass
        _time_mod.sleep(0.5)


threading.Thread(target=_survey_loop, name="SurveyLoop", daemon=True).start()


@app.route("/api/soil/read")
def soil_read():
    return jsonify({"ok": True, "moisture": _soil.read_percent(),
                    "simulate": _soil.simulate})


@app.route("/api/soil/sample", methods=["POST"])
def soil_sample():
    g = _gps.data
    if not g.get("fix"):
        return jsonify({"ok": False, "error": "no gps fix"})
    pct = _soil.read_percent()
    poi = _track_store.add_poi("soil", g["lat"], g["lon"], {"moisture": pct})
    return jsonify({"ok": True, "poi": poi})


@app.route("/api/weeds/sample", methods=["POST"])
def weeds_sample():
    frame = _rgb_cam.read()
    if frame is None:
        return jsonify({"ok": False, "error": "no camera frame"})
    res = _analyze_frame(frame)
    cls = _classify_weed(frame)
    if cls:
        res["weed"] = cls[0]
        res["conf"] = cls[1]
    g = _gps.data
    if not g.get("fix"):
        return jsonify({"ok": True, "analysis": res, "mapped": False})
    poi = _track_store.add_poi("weed", g["lat"], g["lon"], res)
    return jsonify({"ok": True, "analysis": res, "poi": poi})


@app.route("/api/survey/<state>", methods=["POST"])
def survey_toggle(state):
    _survey["on"] = (state == "on")
    if state == "on":
        _survey["last"] = None
    return jsonify({"ok": True, "survey": _survey["on"]})


@app.route("/api/survey/status")
def survey_status():
    return jsonify({
        "on": _survey["on"],
        "min_dist_m": _survey["min_dist_m"],
        "soil_simulate": _soil.simulate,
        "gps_simulate": _gps.simulate,
        "lidar_simulate": _lidar.simulate,
        "cam_ready": _rgb_cam.backend is not None
    })


# ════════════════════════════════════════════════════════════════
# ── خطة 05: الداشبورد الجديد (يستبدل الصفحة الرئيسية) ──
# ════════════════════════════════════════════════════════════════
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/legacy")
def legacy_controller():
    """الواجهة القديمة — للرجوع إليها إن لزم."""
    return render_template("spider_gait_controller.html")


# ── تحديث الصفحة الرئيسية لتفتح الداشبورد مباشرة ──
@app.route("/", endpoint="root_dashboard")
def root_dashboard():
    return render_template("dashboard.html")


# ════════════════════════════════════════════════════════════════
# ── خطة 11: واجهة API خارجية موحّدة /api/v1 (قراءة للأجهزة الخارجية) ──
#    توثيق كامل: docs/API.md
# ════════════════════════════════════════════════════════════════
API_VERSION = "1.0"
import time as _v1_time


def _v1_imu():
    r = read_bno_single()
    return {"ready": bool(r), **(r or {})}


def _v1_gps():
    d = dict(_gps.data)
    d["simulate"] = _gps.simulate
    return d


def _v1_lidar():
    scan = _lidar.get_scan()
    vals = [v for v in scan.values() if v]
    return {
        "ready": _lidar.ready, "simulate": _lidar.simulate, "kind": _lidar.kind,
        "points": len(scan),
        "nearest_mm": round(min(vals)) if vals else None,
        "scan": {str(a): round(v) for a, v in scan.items()},
    }


def _v1_soil():
    return {"moisture_pct": _soil.read_percent(), "raw": _soil.read_raw(),
            "ready": _soil.ready, "simulate": _soil.simulate}


def _v1_environment():
    air = _dht22.read()
    return {"gas_alarm": _gas.alarm(), "gas_simulate": _gas.simulate,
            "air_temp_c": air["temp"], "air_humidity_pct": air["humidity"],
            "dht_simulate": _dht22.simulate}


def _v1_thermal(full=False):
    m = _thermal_cam.read_matrix()
    if m is None:
        return {"ready": False, "mode": _thermal_cam.mode}
    out = {"ready": True, "mode": _thermal_cam.mode,
           "shape": list(m.shape),
           "min_c": round(float(m.min()), 1),
           "max_c": round(float(m.max()), 1),
           "avg_c": round(float(m.mean()), 1)}
    if full:
        out["matrix"] = [[round(float(v), 1) for v in row] for row in m.tolist()]
    return out


def _v1_servos():
    legs = {k: current[k] for k in current}
    return {"legs": legs, "aux": _aux.get_state()}


def _jpeg_response(frame, quality=80):
    import cv2
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return jsonify({"ok": False, "error": "encode failed"}), 500
    return Response(jpg.tobytes(), mimetype="image/jpeg")


@app.route("/api/v1/health")
def v1_health():
    return jsonify({
        "ok": True, "api_version": API_VERSION, "ts": _v1_time.time(),
        "hardware": HARDWARE,
        "sources": {
            "imu": BNO_READY, "gps_sim": _gps.simulate, "lidar_sim": _lidar.simulate,
            "soil_sim": _soil.simulate, "gas_sim": _gas.simulate,
            "dht_sim": _dht22.simulate,
            "rgb_cam": _rgb_cam.backend is not None, "thermal_mode": _thermal_cam.mode,
        },
    })


@app.route("/api/v1/readings")
def v1_readings():
    """لقطة مجمّعة لكل القراءات — الأنسب لجهاز التحليل ليسحبها دورياً."""
    return jsonify({
        "ts": _v1_time.time(), "api_version": API_VERSION,
        "imu": _v1_imu(), "gps": _v1_gps(), "lidar": _v1_lidar(),
        "soil": _v1_soil(), "environment": _v1_environment(),
        "thermal": _v1_thermal(full=False), "servos": _v1_servos(),
    })


@app.route("/api/v1/imu")
def v1_imu():
    return jsonify(_v1_imu())


@app.route("/api/v1/gps")
def v1_gps():
    return jsonify(_v1_gps())


@app.route("/api/v1/lidar")
def v1_lidar():
    return jsonify(_v1_lidar())


@app.route("/api/v1/soil")
def v1_soil():
    return jsonify(_v1_soil())


@app.route("/api/v1/environment")
def v1_environment():
    return jsonify(_v1_environment())


@app.route("/api/v1/thermal")
def v1_thermal():
    full = request.args.get("full", "0") in ("1", "true", "yes")
    return jsonify(_v1_thermal(full=full))


@app.route("/api/v1/servos")
def v1_servos():
    return jsonify(_v1_servos())


@app.route("/api/v1/camera/rgb.jpg")
def v1_camera_rgb():
    """لقطة JPEG مفردة من الكاميرا العادية (GET)."""
    frame = _rgb_cam.read()
    if frame is None:
        return jsonify({"ok": False, "error": "rgb camera unavailable"}), 503
    return _jpeg_response(frame, int(request.args.get("q", 80)))


@app.route("/api/v1/camera/thermal.jpg")
def v1_camera_thermal():
    """لقطة JPEG ملوّنة مفردة من الكاميرا الحرارية (GET)."""
    img = _thermal_cam.colorized()
    if img is None:
        return jsonify({"ok": False, "error": "thermal camera unavailable"}), 503
    return _jpeg_response(img, int(request.args.get("q", 85)))


@app.route("/api/v1/aux_servo", methods=["GET", "POST"])
def v1_aux_servo():
    """GET = الحالة، POST {which:camera|soil, angle:0..180} = ضبط."""
    if request.method == "GET":
        return jsonify({"ok": True, "state": _aux.get_state()})
    d = request.json or {}
    ok, res = _aux.set_by_name(d.get("which"), d.get("angle", 90))
    if not ok:
        return jsonify({"ok": False, "error": res}), 400
    return jsonify({"ok": True, "angle": res, "state": _aux.get_state()})


@app.route("/api/v1/config")
def v1_config():
    """يكشف الإعدادات الفعّالة (من config/sensors.json) للاطّلاع."""
    from spider import config as _cfg
    return jsonify({"ok": True, "sensors": _cfg.SENSORS})


# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"Hardware: {'CONNECTED' if HARDWARE else 'SIMULATION MODE'}")
    print(f"IMU (BNO085): {'READY' if BNO_READY else 'NOT FOUND'}")
    _startup_imu_zero()
    apply_startup_calibration()  # ← المحركات تشتغل بالبداية زي ما كانت
    print("Spider Web Controller → http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
