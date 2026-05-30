from adafruit_servokit import ServoKit
import time
import serial
from adafruit_bno08x_rvc import BNO08x_RVC

right_pca = ServoKit(channels=16, address=0x40)
left_pca = ServoKit(channels=16, address=0x44)

uart = serial.Serial("/dev/serial0", 115200, timeout=1)
bno = BNO08x_RVC(uart)

MIN_ANGLE = 45
MAX_ANGLE = 135
MOVE_DELAY = 0.02

cal = {
    "R": {
        0: 90,
        1: 75,
        2: 92,
        3: 92,
        4: 65,
        5: 96,
        6: 93,
        7: 65,
        8: 94
    },
    "L": {
        0: 90,
        1: 59,
        2: 81,
        3: 85,
        4: 76,
        5: 95,
        6: 94,
        7: 66,
        8: 98
    }
}

pca = {
    "R": right_pca,
    "L": left_pca
}

zero_roll = 0.0
zero_pitch = 0.0


def limit_angle(angle):
    return max(MIN_ANGLE, min(MAX_ANGLE, int(angle)))


def set_channel(side, ch, angle):
    side = side.upper()
    ch = int(ch)
    angle = limit_angle(angle)

    if side not in ["R", "L"]:
        print("Use R or L")
        return

    if ch < 0 or ch > 8:
        print("Use channel 0 to 8")
        return

    pca[side].servo[ch].angle = angle
    cal[side][ch] = angle
    print(side + str(ch), "=", angle)


def move_channel_smooth(side, ch, target):
    side = side.upper()
    ch = int(ch)
    target = limit_angle(target)

    start = cal[side][ch]

    if start == target:
        set_channel(side, ch, target)
        return

    step = 1 if target > start else -1

    for angle in range(start, target + step, step):
        pca[side].servo[ch].angle = angle
        cal[side][ch] = angle
        time.sleep(MOVE_DELAY)

    print(side + str(ch), "=", target)


def apply_all():
    print("Applying RIGHT")
    for ch in range(9):
        move_channel_smooth("R", ch, cal["R"][ch])
        time.sleep(0.05)

    print("Applying LEFT")
    for ch in range(9):
        move_channel_smooth("L", ch, cal["L"][ch])
        time.sleep(0.05)

    print("Stance applied")


def step_channel(side, ch, delta):
    side = side.upper()
    ch = int(ch)
    delta = int(delta)
    set_channel(side, ch, cal[side][ch] + delta)


def off_all():
    for ch in range(16):
        right_pca.servo[ch].angle = None
        left_pca.servo[ch].angle = None

    print("All servos off")


def read_bno_average(samples=10):
    rolls = []
    pitches = []
    yaws = []

    for _ in range(samples):
        try:
            yaw, pitch, roll, ax, ay, az = bno.heading
            rolls.append(roll)
            pitches.append(pitch)
            yaws.append(yaw)
        except Exception as e:
            print("BNO read error:", e)

        time.sleep(0.06)

    if len(rolls) == 0:
        return None, None, None

    return sum(rolls) / len(rolls), sum(pitches) / len(pitches), sum(yaws) / len(yaws)


def zero_bno():
    global zero_roll, zero_pitch

    print("Keep robot still...")
    time.sleep(1)

    roll, pitch, yaw = read_bno_average(20)

    if roll is None:
        print("Cannot zero BNO")
        return

    zero_roll = roll
    zero_pitch = pitch

    print("ZERO_ROLL =", round(zero_roll, 2))
    print("ZERO_PITCH =", round(zero_pitch, 2))


def read_bno():
    roll, pitch, yaw = read_bno_average(10)

    if roll is None:
        print("BNO read failed")
        return

    corrected_roll = roll - zero_roll
    corrected_pitch = pitch - zero_pitch

    print("")
    print("Raw Roll        =", round(roll, 2))
    print("Raw Pitch       =", round(pitch, 2))
    print("Corrected Roll  =", round(corrected_roll, 2))
    print("Corrected Pitch =", round(corrected_pitch, 2))
    print("Yaw             =", round(yaw, 2))
    print("")


def status():
    print("")
    print("RIGHT PCA 0x40")
    print("R0 =", cal["R"][0], "R1 =", cal["R"][1], "R2 =", cal["R"][2])
    print("R3 =", cal["R"][3], "R4 =", cal["R"][4], "R5 =", cal["R"][5])
    print("R6 =", cal["R"][6], "R7 =", cal["R"][7], "R8 =", cal["R"][8])
    print("")
    print("LEFT PCA 0x44")
    print("L0 =", cal["L"][0], "L1 =", cal["L"][1], "L2 =", cal["L"][2])
    print("L3 =", cal["L"][3], "L4 =", cal["L"][4], "L5 =", cal["L"][5])
    print("L6 =", cal["L"][6], "L7 =", cal["L"][7], "L8 =", cal["L"][8])
    print("")


def save_values():
    print("")
    print("SAVE THESE VALUES")
    print("")
    print("R")
    for ch in range(9):
        print(str(ch) + "=" + str(cal["R"][ch]))
    print("")
    print("L")
    for ch in range(9):
        print(str(ch) + "=" + str(cal["L"][ch]))
    print("")


def help_menu():
    print("")
    print("Commands:")
    print("apply")
    print("zero")
    print("read")
    print("set R CHANNEL ANGLE")
    print("set L CHANNEL ANGLE")
    print("step R CHANNEL DELTA")
    print("step L CHANNEL DELTA")
    print("status")
    print("save")
    print("off")
    print("exit")
    print("")
    print("Examples:")
    print("step L 1 2")
    print("step L 4 2")
    print("step R 1 -2")
    print("set R 7 67")
    print("")


status()
help_menu()

try:
    while True:
        cmd = input("cmd> ").strip().split()

        if len(cmd) == 0:
            continue

        if cmd[0] == "apply":
            apply_all()
            continue

        if cmd[0] == "zero":
            zero_bno()
            continue

        if cmd[0] == "read":
            read_bno()
            continue

        if cmd[0] == "status":
            status()
            continue

        if cmd[0] == "save":
            save_values()
            continue

        if cmd[0] == "off":
            off_all()
            continue

        if cmd[0] == "exit":
            off_all()
            break

        if cmd[0] == "set" and len(cmd) == 4:
            set_channel(cmd[1], cmd[2], cmd[3])
            continue

        if cmd[0] == "step" and len(cmd) == 4:
            step_channel(cmd[1], cmd[2], cmd[3])
            continue

        if cmd[0] == "help":
            help_menu()
            continue

        print("Invalid command")

except KeyboardInterrupt:
    off_all()
    print("Stopped")
