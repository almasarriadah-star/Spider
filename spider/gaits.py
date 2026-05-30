# spider/gaits.py
"""محرّكات المشي: Forward Tripod, Lateral, Ripple, Smooth Ripple + مساعدات."""
import json
import math
import os
import time
import threading

from spider.safety import arbiter
from spider.constants import LEG_GROUPS

BASE_DIR           = os.path.dirname(os.path.dirname(__file__))
CONFIG_DIR         = os.path.join(BASE_DIR, "config")
GAIT_FILE          = os.path.join(CONFIG_DIR, "gait_params.json")
RIPPLE_PARAMS_FILE = os.path.join(CONFIG_DIR, "ripple_gait_params.json")
MOTION_FILE        = os.path.join(CONFIG_DIR, "motion.json")

# ── قيم motion.json (الثوابت المُخرجة) ──
def _load_motion():
    try:
        with open(MOTION_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"lateral": {"shift_deg": 14, "lift_deg": 14}, "body": {"delta": 12}}

_motion = _load_motion()


def reload_motion():
    """إعادة تحميل motion.json حياً بلا إعادة تشغيل السيرفر."""
    global _motion
    _motion = _load_motion()
    print("[gaits] motion.json reloaded")


# ────── Easing Functions ──────
def ease_smoothstep(t):
    """Hermite smoothstep — بداية ونهاية ناعمة"""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def ease_cosine(t):
    """Cosine ease — أنعم من smoothstep"""
    return (1 - math.cos(t * math.pi)) / 2

_ease_func = ease_smoothstep


def set_ease(name):
    """يغيّر دالة الـ easing الافتراضية."""
    global _ease_func
    if name == "linear":
        _ease_func = lambda t: t
    elif name == "cosine":
        _ease_func = ease_cosine
    elif name == "smoothstep":
        _ease_func = ease_smoothstep
    else:
        raise ValueError(f"Unknown easing: {name}")


def get_ease_name():
    return _ease_func.__name__ if hasattr(_ease_func, "__name__") else "custom"


# ────── Loaders ──────
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


def _load_ripple_params():
    """يحمّل بيانات Ripple Gait."""
    try:
        with open(RIPPLE_PARAMS_FILE) as f:
            return json.load(f)
    except Exception:
        return None


# ────── Smooth Move Helper ──────
def smooth_move_leg(keys, targets, steps=10, delay=0.03, easing=None):
    """Smoothly move a set of servos to target angles with easing."""
    from spider.safety import current, _force_stop_ref
    if easing is None:
        easing = _ease_func
    starts = {k: current[k] for k in keys}
    for step in range(1, steps + 1):
        if _force_stop_ref():
            return
        t = step / steps
        t_eased = easing(t)
        batch = {}
        for k in keys:
            if k in targets:
                angle = starts[k] + (targets[k] - starts[k]) * t_eased
                batch[k] = round(angle)
        owner = arbiter.owner()
        arbiter.set_target(owner, batch)
        time.sleep(delay)


# ────── Gait Engine: get_leg_angles ──────
def get_leg_angles(leg_name, frame, params, gait_type='forward'):
    """حساب زوايا الرجل لأي فريم في دورة المشي."""
    leg = params["legs"][leg_name]
    total = params["total_frames"]
    amp = params["coxa_amplitude"]
    grp_a = params["groups"]["A"]

    offset = 0 if leg_name in grp_a else total // 2
    f = (frame + offset) % total
    half = total // 2
    side = leg["side"]

    direction = 1 if side == "R" else -1

    if gait_type == 'backward':
        direction = -direction
    if gait_type == 'turn_left':
        if side == 'L':
            direction = -direction
    if gait_type == 'turn_right':
        if side == 'R':
            direction = -direction

    coxa_angle_offset = 0
    if gait_type == 'shift_left':
        coxa_angle_offset = -18
        amp = amp * 0.7
    elif gait_type == 'shift_right':
        coxa_angle_offset = 18
        amp = amp * 0.7
    elif gait_type in ('strafe_left', 'crab_walk'):
        coxa_angle_offset = -18
    elif gait_type == 'strafe_right':
        coxa_angle_offset = 18

    femur_lift_extra = 0
    femur_stance_offset = 0
    if gait_type == 'climb':
        femur_lift_extra = 15
    elif gait_type == 'prowl':
        femur_stance_offset = 18
        amp = amp * 0.5
    elif gait_type == 'high_step':
        femur_lift_extra = 25
        amp = amp * 0.8
    elif gait_type == 'glide':
        femur_lift_extra = -(leg["femur_lift"] - leg["femur_stand"] - 8)
        amp = int(amp * 1.15)

    base_femur = leg["femur_stand"] + femur_stance_offset

    if f < half:
        progress = f / half
        coxa = leg["coxa_stand"] + direction * (-amp + 2 * amp * progress) + coxa_angle_offset
        femur = base_femur + (leg["femur_lift"] + femur_lift_extra - base_femur) \
                * math.sin(progress * math.pi)
    else:
        progress = (f - half) / half
        coxa = leg["coxa_stand"] + direction * (amp - 2 * amp * progress) + coxa_angle_offset
        femur = base_femur

    tibia = leg["tibia"]

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


# ────── Forward Tripod Gait ──────
def _run_forward_gait(params, speed=1.0, max_cycles=0, gait_type='forward',
                      gait_event=None, gait_running_ref=None, balance=None,
                      on_done=None, step_counter_ref=None, phase_ref=None):
    """تنفيذ Forward Tripod Gait في thread خلفية مع مراعاة محكّم الحركة."""

    if not arbiter.acquire("gait"):
        print("Gait execution rejected: motion arbiter busy with owner", arbiter.owner())
        if gait_running_ref:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        return

    try:
        effective_speed = speed
        if gait_type == 'creep':
            effective_speed = speed * 0.3

        delay   = (params["frame_delay_ms"] / 1000.0) / effective_speed
        s_steps = max(3, int(params["smooth_steps"] / effective_speed))
        s_delay = max(0.01, params["smooth_delay"] / effective_speed)
        total   = params["total_frames"]
        legs    = list(params["legs"].keys())
        frame   = 0
        cycles  = 0

        while gait_event.is_set() and not arbiter.in_estop():
            if max_cycles > 0 and cycles >= max_cycles:
                break

            all_targets = {}
            for leg in legs:
                angles = get_leg_angles(leg, frame, params, gait_type=gait_type)
                all_targets.update(angles)

            # Balance correction
            if balance and balance.enabled:
                grp_a = params["groups"]["A"]
                half  = total // 2
                f_a   = frame % total
                for leg_name in legs:
                    leg = params["legs"][leg_name]
                    side = leg["side"]
                    leg_idx = list(params["legs"].keys()).index(leg_name)
                    ch = leg_idx * 3 if side == "R" else (leg_idx - 3) * 3
                    femur_key = f"{side}{ch+1}"
                    offset   = 0 if leg_name in grp_a else half
                    f_leg    = (f_a + offset) % total
                    is_swing = f_leg < half
                    if not is_swing and femur_key in all_targets:
                        correction = balance.femur_offsets.get(leg_name, 0.0)
                        all_targets[femur_key] = max(45, min(135, round(all_targets[femur_key] + correction)))

            if phase_ref is not None:
                phase_ref[0] = f"frame-{frame}"
            smooth_move_leg(list(all_targets.keys()), all_targets, steps=s_steps, delay=s_delay)

            frame += 1
            if frame >= total:
                frame = 0
                cycles += 1
                if step_counter_ref is not None:
                    step_counter_ref[0] += 1

            remaining = delay - s_steps * s_delay
            time.sleep(max(0, remaining))

        # إيقاف نظيف — ارجع لوضعية الوقوف
        if phase_ref is not None:
            phase_ref[0] = "returning"
        stance_targets = {}
        for leg_name, leg_data in params["legs"].items():
            side    = leg_data["side"]
            leg_idx = list(params["legs"].keys()).index(leg_name)
            ch      = leg_idx * 3 if side == "R" else (leg_idx - 3) * 3
            stance_targets[f"{side}{ch}"]   = leg_data["coxa_stand"]
            stance_targets[f"{side}{ch+1}"] = leg_data["femur_stand"]
            stance_targets[f"{side}{ch+2}"] = leg_data["tibia"]
        smooth_move_leg(list(stance_targets.keys()), stance_targets, steps=12, delay=0.03)
    finally:
        if phase_ref is not None:
            phase_ref[0] = "idle"
        if gait_running_ref is not None:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        arbiter.release("gait")


# ────── Lateral Gait ──────
def _run_lateral_gait(params, speed=1.0, max_cycles=0, direction='left',
                      gait_event=None, gait_running_ref=None, balance=None,
                      step_counter_ref=None, phase_ref=None):
    """حركة جانبية حقيقية باستخدام مفصل الـ Tibia."""

    if not arbiter.acquire("gait"):
        print("Lateral gait execution rejected: motion arbiter busy with owner", arbiter.owner())
        if gait_running_ref:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        return

    try:
        SHIFT_DEG = _motion.get("lateral", {}).get("shift_deg", 14)
        LIFT_DEG  = _motion.get("lateral", {}).get("lift_deg", 14)
        total     = 8
        half      = total // 2
        delay     = 0.10 / speed
        s_steps   = max(3, int(6 / speed))
        s_delay   = max(0.01, 0.02 / speed)
        sign      = 1 if direction == 'left' else -1

        grp_a      = params["groups"]["A"]
        legs_all   = list(params["legs"].keys())
        legs_order = list(params["legs"].keys())

        def _servo_keys(leg_name):
            leg = params["legs"][leg_name]
            side = leg["side"]
            leg_idx = legs_order.index(leg_name)
            ch = leg_idx * 3 if side == "R" else (leg_idx - 3) * 3
            return f"{side}{ch}", f"{side}{ch+1}", f"{side}{ch+2}"

        frame  = 0
        cycles = 0

        while gait_event.is_set() and not arbiter.in_estop():
            if max_cycles > 0 and cycles >= max_cycles:
                break

            all_targets = {}
            for leg_name in legs_all:
                leg  = params["legs"][leg_name]
                side = leg["side"]
                c_key, f_key, t_key = _servo_keys(leg_name)
                offset  = 0 if leg_name in grp_a else half
                f_local = (frame + offset) % total
                lat_dir = (1 if side == 'R' else -1) * sign

                if f_local < half:
                    progress = f_local / half
                    coxa  = leg["coxa_stand"]
                    femur = leg["femur_stand"] + LIFT_DEG * math.sin(progress * math.pi)
                    tibia = leg["tibia"] + lat_dir * (SHIFT_DEG - 2 * SHIFT_DEG * progress)
                else:
                    progress = (f_local - half) / half
                    coxa  = leg["coxa_stand"]
                    femur = leg["femur_stand"]
                    tibia = leg["tibia"] + lat_dir * (-SHIFT_DEG + 2 * SHIFT_DEG * progress)

                all_targets[c_key] = max(45, min(135, round(coxa)))
                all_targets[f_key] = max(45, min(135, round(femur)))
                all_targets[t_key] = max(45, min(135, round(tibia)))

            if balance and balance.enabled:
                for leg_name in legs_all:
                    leg  = params["legs"][leg_name]
                    side = leg["side"]
                    _, f_key, _ = _servo_keys(leg_name)
                    offset  = 0 if leg_name in grp_a else half
                    f_leg   = (frame + offset) % total
                    is_swing = f_leg < half
                    if not is_swing and f_key in all_targets:
                        correction = balance.femur_offsets.get(leg_name, 0.0)
                        all_targets[f_key] = max(45, min(135, round(all_targets[f_key] + correction)))

            if phase_ref is not None:
                phase_ref[0] = f"lateral-{frame}"
            smooth_move_leg(list(all_targets.keys()), all_targets, steps=s_steps, delay=s_delay)

            frame += 1
            if frame >= total:
                frame = 0
                cycles += 1
                if step_counter_ref is not None:
                    step_counter_ref[0] += 1

            remaining = delay - s_steps * s_delay
            time.sleep(max(0, remaining))

        if phase_ref is not None:
            phase_ref[0] = "returning"
        stance_targets = {}
        for leg_name in legs_all:
            leg = params["legs"][leg_name]
            c_key, f_key, t_key = _servo_keys(leg_name)
            stance_targets[c_key] = leg["coxa_stand"]
            stance_targets[f_key] = leg["femur_stand"]
            stance_targets[t_key] = leg["tibia"]
        smooth_move_leg(list(stance_targets.keys()), stance_targets, steps=12, delay=0.03)
    finally:
        if phase_ref is not None:
            phase_ref[0] = "idle"
        if gait_running_ref is not None:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        arbiter.release("gait")


# ────── Foot Arc Trajectory + Tibia Compensation ──────
def foot_arc_trajectory(phase, stride_length, lift_height):
    """حساب مسار القدم — قوس D ناعم."""
    if phase < 0.5:
        t = phase / 0.5
        t_smooth = ease_smoothstep(t)
        coxa_offset = -stride_length / 2 + stride_length * t_smooth
        femur_lift  = lift_height * math.sin(t * math.pi)
    else:
        t = (phase - 0.5) / 0.5
        t_smooth    = ease_smoothstep(t)
        coxa_offset = stride_length / 2 - stride_length * t_smooth
        femur_lift  = 0.0
    return coxa_offset, femur_lift


def tibia_compensation(lift_ratio, max_comp=0.5):
    """تعويض Tibia — يعاكس Femur عشان القدم تبقى أفقية نسبياً."""
    comp = ease_smoothstep(lift_ratio) * max_comp
    return comp


# ────── Ripple Gait ──────
def _run_ripple_gait(params, speed=1.0, max_cycles=0, direction='forward',
                     gait_event=None, gait_running_ref=None, balance=None,
                     step_counter_ref=None, phase_ref=None):
    """تنفيذ Ripple Gait — رجل واحدة بالهوا بالتسلسل."""

    if not arbiter.acquire("gait"):
        print("Ripple gait execution rejected: motion arbiter busy with owner", arbiter.owner())
        if gait_running_ref:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        return

    try:
        RIPPLE_ORDER = params.get("RIPPLE_ORDER", ["RR", "RM", "RF", "LR", "LM", "LF"])
        LIFT_DEG     = params.get("LIFT_DEG", 14)
        SWING_DEG    = params.get("SWING_DEG", 20)
        TOTAL_FRAMES = params.get("total_frames", 60)
        delay        = (params.get("frame_delay_ms", 20) / 1000.0) / speed
        frame        = 0
        cycles       = 0
        legs_order   = list(params["legs"].keys())

        while gait_event.is_set() and not arbiter.in_estop():
            if max_cycles > 0 and cycles >= max_cycles:
                break

            t_global    = frame / TOTAL_FRAMES
            all_targets = {}

            for idx, leg_name in enumerate(RIPPLE_ORDER):
                leg  = params["legs"][leg_name]
                side = leg["side"]

                coxa_body_offset = 0
                if direction == 'strafe_left':
                    coxa_body_offset = -18
                    sign = 1 if side == "R" else -1
                elif direction == 'strafe_right':
                    coxa_body_offset = 18
                    sign = 1 if side == "R" else -1
                elif direction == 'turn_left':
                    sign = 1 if side == "R" else -1
                elif direction == 'turn_right':
                    sign = -1 if side == "R" else 1
                else:
                    sign = 1 if side == "R" else -1
                    if direction == 'backward':
                        sign = -sign

                leg_phase = (t_global + idx / len(RIPPLE_ORDER)) % 1.0
                DUTY       = 5.0 / 6.0
                SWING_FRAC = 1.0 - DUTY

                if leg_phase < SWING_FRAC:
                    swing_t      = leg_phase / SWING_FRAC
                    swing_t_ease = ease_smoothstep(swing_t)
                    coxa_offset  = (-SWING_DEG/2 + SWING_DEG * swing_t_ease) * sign
                    lift         = LIFT_DEG * math.sin(math.pi * swing_t)
                else:
                    stance_t      = (leg_phase - SWING_FRAC) / DUTY
                    stance_t_ease = ease_smoothstep(stance_t)
                    coxa_offset   = (SWING_DEG/2 - SWING_DEG * stance_t_ease) * sign
                    lift          = 0

                coxa  = leg["coxa_stand"]  + coxa_offset + coxa_body_offset
                femur = leg["femur_stand"] + lift
                tibia = leg["tibia"]       - lift * 0.4

                if balance and balance.enabled and lift < 0.5:
                    femur += balance.femur_offsets.get(leg_name, 0.0)

                leg_idx = legs_order.index(leg_name)
                ch = leg_idx * 3 if side == "R" else (leg_idx - 3) * 3

                all_targets[f"{side}{ch}"]   = max(45, min(135, round(coxa)))
                all_targets[f"{side}{ch+1}"] = max(45, min(135, round(femur)))
                all_targets[f"{side}{ch+2}"] = max(45, min(135, round(tibia)))

            if phase_ref is not None:
                phase_ref[0] = f"ripple-{frame}"
            smooth_move_leg(list(all_targets.keys()), all_targets, steps=3, delay=0.01)

            frame += 1
            if frame >= TOTAL_FRAMES:
                frame = 0
                cycles += 1
                if step_counter_ref is not None:
                    step_counter_ref[0] += 1

            time.sleep(delay)

        if phase_ref is not None:
            phase_ref[0] = "returning"
        stance_targets = {}
        for leg_name, leg_data in params["legs"].items():
            side    = leg_data["side"]
            leg_idx = list(params["legs"].keys()).index(leg_name)
            ch      = leg_idx * 3 if side == "R" else (leg_idx - 3) * 3
            stance_targets[f"{side}{ch}"]   = leg_data["coxa_stand"]
            stance_targets[f"{side}{ch+1}"] = leg_data["femur_stand"]
            stance_targets[f"{side}{ch+2}"] = leg_data["tibia"]
        smooth_move_leg(list(stance_targets.keys()), stance_targets, steps=12, delay=0.03)
    finally:
        if phase_ref is not None:
            phase_ref[0] = "idle"
        if gait_running_ref is not None:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        arbiter.release("gait")


# ────── Smooth Ripple Gait ──────
COXA_DIRECTION = {"RF": +1, "RM": +1, "RR": +1, "LF": -1, "LM": -1, "LR": -1}
TIBIA_DIRECTION = {"RF": +1, "RM": +1, "RR": +1, "LF": -1, "LM": -1, "LR": -1}


def _run_smooth_ripple(params, speed=1.0, max_cycles=0, direction='forward',
                       gait_event=None, gait_running_ref=None, balance=None,
                       step_counter_ref=None, phase_ref=None):
    """Ripple Gait محسّن مع Foot Trajectory + Tibia ديناميكي + Balance"""

    if not arbiter.acquire("gait"):
        print("Smooth ripple gait execution rejected: motion arbiter busy with owner", arbiter.owner())
        if gait_running_ref:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        return

    try:
        RIPPLE_ORDER = params.get("RIPPLE_ORDER", ["RR", "RM", "RF", "LR", "LM", "LF"])
        LIFT_DEG     = params.get("LIFT_DEG", 14)
        SWING_DEG    = params.get("SWING_DEG", 20)
        TOTAL_FRAMES = params.get("total_frames", 60)
        delay        = (params.get("frame_delay_ms", 20) / 1000.0) / speed
        frame        = 0
        cycles       = 0
        phase_offset = 1.0 / len(RIPPLE_ORDER)
        legs_order   = list(params["legs"].keys())

        while gait_event.is_set() and not arbiter.in_estop():
            if max_cycles > 0 and cycles >= max_cycles:
                break

            global_phase = frame / TOTAL_FRAMES
            all_targets  = {}

            for idx, leg_name in enumerate(RIPPLE_ORDER):
                leg      = params["legs"][leg_name]
                side     = leg["side"]
                leg_phase = (global_phase + idx * phase_offset) % 1.0

                coxa_body_offset = 0
                if direction == 'strafe_left':
                    coxa_body_offset = -18
                    sign = 1 if side == "R" else -1
                elif direction == 'strafe_right':
                    coxa_body_offset = 18
                    sign = 1 if side == "R" else -1
                elif direction == 'turn_left':
                    sign = 1 if side == "R" else 1
                elif direction == 'turn_right':
                    sign = -1 if side == "R" else -1
                else:
                    sign = 1 if side == "R" else -1
                    if direction == 'backward':
                        sign = -sign

                stride = SWING_DEG * abs(sign) if sign != 0 else SWING_DEG
                coxa_off, femur_lift = foot_arc_trajectory(leg_phase, stride, LIFT_DEG)

                lift_ratio = femur_lift / LIFT_DEG if LIFT_DEG > 0 else 0
                tibia_off  = -femur_lift * tibia_compensation(lift_ratio, max_comp=0.5)

                balance_off = 0.0
                if balance and balance.enabled and femur_lift < 0.5:
                    balance_off = balance.femur_offsets.get(leg_name, 0.0)

                coxa_target  = leg["coxa_stand"]  + coxa_off * sign + coxa_body_offset
                femur_target = leg["femur_stand"]  + femur_lift + balance_off
                tibia_target = leg["tibia"]        - femur_lift * 0.4

                coxa_target  = max(50, min(130, round(coxa_target)))
                femur_target = max(50, min(130, round(femur_target)))
                tibia_target = max(50, min(130, round(tibia_target)))

                leg_idx = legs_order.index(leg_name)
                ch = leg_idx * 3 if side == "R" else (leg_idx - 3) * 3

                all_targets[f"{side}{ch}"]   = coxa_target
                all_targets[f"{side}{ch+1}"] = femur_target
                all_targets[f"{side}{ch+2}"] = tibia_target

            if phase_ref is not None:
                phase_ref[0] = f"smooth-ripple-{frame}"
            smooth_move_leg(list(all_targets.keys()), all_targets, steps=3, delay=0.01)

            frame += 1
            if frame >= TOTAL_FRAMES:
                frame = 0
                cycles += 1
                if step_counter_ref is not None:
                    step_counter_ref[0] += 1

            remaining = delay - 3 * 0.01
            time.sleep(max(0, remaining))

        if phase_ref is not None:
            phase_ref[0] = "returning"
        stance_targets = {}
        for leg_name, leg_data in params["legs"].items():
            side    = leg_data["side"]
            leg_idx = list(params["legs"].keys()).index(leg_name)
            ch      = leg_idx * 3 if side == "R" else (leg_idx - 3) * 3
            stance_targets[f"{side}{ch}"]   = leg_data["coxa_stand"]
            stance_targets[f"{side}{ch+1}"] = leg_data["femur_stand"]
            stance_targets[f"{side}{ch+2}"] = leg_data["tibia"]
        smooth_move_leg(list(stance_targets.keys()), stance_targets, steps=12, delay=0.03)
    finally:
        if phase_ref is not None:
            phase_ref[0] = "idle"
        if gait_running_ref is not None:
            gait_running_ref[0] = False
        if gait_event:
            gait_event.clear()
        arbiter.release("gait")
