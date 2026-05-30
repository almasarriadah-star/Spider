from adafruit_servokit import ServoKit
import time

right_pca = ServoKit(channels=16, address=0x40)
left_pca = ServoKit(channels=16, address=0x44)

MIN_ANGLE = 45
MAX_ANGLE = 135
MOVE_DELAY = 0.025

RIGHT_CAL = {
    0: 90,
    1: 75,
    2: 92,
    3: 92,
    4: 65,
    5: 96,
    6: 93,
    7: 65,
    8: 94
}

LEFT_CAL = {
    0: 90,
    1: 57,
    2: 81,
    3: 85,
    4: 74,
    5: 95,
    6: 94,
    7: 66,
    8: 98
}

current_R = {ch: 90 for ch in range(9)}
current_L = {ch: 90 for ch in range(9)}


def limit_angle(angle):
    return max(MIN_ANGLE, min(MAX_ANGLE, int(angle)))


def set_right(channel, angle):
    angle = limit_angle(angle)
    right_pca.servo[channel].angle = angle
    current_R[channel] = angle
    print("R", channel, "=", angle)


def set_left(channel, angle):
    angle = limit_angle(angle)
    left_pca.servo[channel].angle = angle
    current_L[channel] = angle
    print("L", channel, "=", angle)


def move_channel_smooth(side, channel, target):
    target = limit_angle(target)

    if side == "R":
        start = current_R[channel]
    else:
        start = current_L[channel]

    if start == target:
        if side == "R":
            set_right(channel, target)
        else:
            set_left(channel, target)
        return

    step = 1 if target > start else -1

    for angle in range(start, target + step, step):
        if side == "R":
            set_right(channel, angle)
        else:
            set_left(channel, angle)

        time.sleep(MOVE_DELAY)


def apply_calibration_smooth():
    print("Moving RIGHT PCA 0x40")
    for ch in range(9):
        move_channel_smooth("R", ch, RIGHT_CAL[ch])
        time.sleep(0.1)

    print("Moving LEFT PCA 0x44")
    for ch in range(9):
        move_channel_smooth("L", ch, LEFT_CAL[ch])
        time.sleep(0.1)

    print("Calibration applied")


def apply_calibration_direct():
    print("Direct apply RIGHT PCA 0x40")
    for ch in range(9):
        set_right(ch, RIGHT_CAL[ch])
        time.sleep(0.05)

    print("Direct apply LEFT PCA 0x44")
    for ch in range(9):
        set_left(ch, LEFT_CAL[ch])
        time.sleep(0.05)

    print("Calibration applied directly")


def off_all():
    for ch in range(16):
        right_pca.servo[ch].angle = None
        left_pca.servo[ch].angle = None

    print("All servos off")


def print_status():
    print("")
    print("RIGHT PCA 0x40")
    print("R0=90  R1=75  R2=92")
    print("R3=92  R4=65  R5=96")
    print("R6=93  R7=65  R8=94")
    print("")
    print("LEFT PCA 0x44")
    print("L0=90  L1=57  L2=81")
    print("L3=85  L4=74  L5=95")
    print("L6=94  L7=66  L8=98")
    print("")


def help_menu():
    print("")
    print("Commands:")
    print("calibrate")
    print("direct")
    print("status")
    print("off")
    print("exit")
    print("")
    print("Use calibrate for smooth movement.")
    print("Use direct for immediate movement.")
    print("Use off if any motor heats or strains.")
    print("")


print_status()
help_menu()

try:
    while True:
        cmd = input("cmd> ").strip().split()

        if len(cmd) == 0:
            continue

        if cmd[0] == "calibrate":
            apply_calibration_smooth()
            continue

        if cmd[0] == "direct":
            apply_calibration_direct()
            continue

        if cmd[0] == "status":
            print_status()
            continue

        if cmd[0] == "off":
            off_all()
            continue

        if cmd[0] == "exit":
            off_all()
            break

        if cmd[0] == "help":
            help_menu()
            continue

        print("Invalid command")

except KeyboardInterrupt:
    off_all()
    print("Stopped")
