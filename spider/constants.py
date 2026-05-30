# spider/constants.py
"""ثوابت الروبوت — مصدر واحد للأسماء والمجموعات والوضعيات الافتراضية."""

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

LEG_GROUPS = {
    "RF": ["R0", "R1", "R2"],
    "RM": ["R3", "R4", "R5"],
    "RR": ["R6", "R7", "R8"],
    "LF": ["L0", "L1", "L2"],
    "LM": ["L3", "L4", "L5"],
    "LR": ["L6", "L7", "L8"],
}

# وضعية الوقوف الأساسية بمفاتيح R0..R8 / L0..L8
_STAND = {}
for _ch, _a in DEFAULT_RIGHT.items():
    _STAND[f"R{_ch}"] = _a
for _ch, _a in DEFAULT_LEFT.items():
    _STAND[f"L{_ch}"] = _a
