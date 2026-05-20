#!/bin/bash
# /home/tariq_alku/Spider/shutdown_servos.sh
# 1. Apply balanced stance
# 2. Then cut PWM

python3 -c "
import sys, time
try:
    from adafruit_servokit import ServoKit
    
    right = ServoKit(channels=16, address=0x40)
    left = ServoKit(channels=16, address=0x44)
    
    # وضعية التوازن (السلوط 1 المُعايرة)
    stance = {
        0: 90, 1: 67, 2: 91,   # RF
        3: 92, 4: 57, 5: 92,   # RM
        6: 93, 7: 65, 8: 94,   # RR
    }
    stance_left = {
        0: 90, 1: 54, 2: 77,   # LF
        3: 85, 4: 74, 5: 94,   # LM
        6: 94, 7: 64, 8: 89,   # LR
    }
    
    for ch, angle in stance.items():
        right.servo[ch].angle = angle
    for ch, angle in stance_left.items():
        left.servo[ch].angle = angle
    
    time.sleep(0.3)
    
    # قطع PWM
    for ch in range(16):
        right._pca.channels[ch].duty_cycle = 0
        left._pca.channels[ch].duty_cycle = 0
    
    print('Balanced stance applied, servos off')
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)
"
