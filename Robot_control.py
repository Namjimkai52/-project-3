#!/usr/bin/env python3
"""
robot_control.py (เวอร์ชันสมบูรณ์: ขับเคลื่อนล้อหน้า + แก้ Servo SG90 กระตุก)
- สลับขั้วมอเตอร์ในซอฟต์แวร์ เพื่อให้ล้อขับเคลื่อนเป็นล้อหน้า
- ปรับค่าความถี่ให้ตรงกับ SG90 (1500us คือจุดศูนย์กลาง)
- เพิ่มคำสั่ง pi.write(PIN, 0) เพื่อดึงขาสัญญาณลงกราวด์ทันทีที่ปล่อยมือ
"""

import struct
import time
import RPi.GPIO as GPIO
import pigpio

# ---------------- ตั้งค่า GPIO: มอเตอร์ ----------------
# สลับขั้ว IN เพื่อพลิกให้ฝั่งล้อขับเคลื่อนกลายเป็น "ด้านหน้า"
IN1, IN2 = 27, 17  # (แก้จาก 17, 27)
IN3, IN4 = 23, 22  # (แก้จาก 22, 23)
PWM_FREQ_MOTOR = 1000

MOVE_SPEED = 80
TURN_SPEED = 70

# ---------------- ตั้งค่า GPIO: Servo 1 & 2 ----------------
SERVO1_PIN = 18
SERVO2_PIN = 19

# จุดหยุดและหมุนสำหรับ SG90 360 องศา (อ้างอิงจุดศูนย์กลางที่ 1500 us)
SERVO_CW_US = 1600   # หมุนทิศทางที่ 1
SERVO_CCW_US = 1400  # หมุนทิศทางที่ 2

# ---------------- เริ่มต้น GPIO (สำหรับมอเตอร์) ----------------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup([IN1, IN2, IN3, IN4], GPIO.OUT)

pwm_in1 = GPIO.PWM(IN1, PWM_FREQ_MOTOR)
pwm_in2 = GPIO.PWM(IN2, PWM_FREQ_MOTOR)
pwm_in3 = GPIO.PWM(IN3, PWM_FREQ_MOTOR)
pwm_in4 = GPIO.PWM(IN4, PWM_FREQ_MOTOR)

for p in (pwm_in1, pwm_in2, pwm_in3, pwm_in4):
    p.start(0)

# ---------------- เริ่มต้น pigpio (สำหรับ Servo) ----------------
pi = pigpio.pi()
if not pi.connected:
    print("ไม่สามารถเชื่อมต่อ pigpiod ได้! กรุณารัน 'sudo pigpiod' ใน Terminal ก่อน")
    exit()

def set_servo1(duty_us):
    """เปิด-ปิดสัญญาณ Servo 1 เฉพาะตอนมีคำสั่ง"""
    if duty_us is None:
        pi.set_servo_pulsewidth(SERVO1_PIN, 0)  # ตัดสัญญาณ PWM
        pi.write(SERVO1_PIN, 0)                 # ดึงสัญญาณลงกราวด์ป้องกันคลื่นแทรก
    else:
        pi.set_servo_pulsewidth(SERVO1_PIN, duty_us)

def set_servo2(duty_us):
    """เปิด-ปิดสัญญาณ Servo 2 เฉพาะตอนมีคำสั่ง"""
    if duty_us is None:
        pi.set_servo_pulsewidth(SERVO2_PIN, 0)  # ตัดสัญญาณ PWM
        pi.write(SERVO2_PIN, 0)                 # ดึงสัญญาณลงกราวด์ป้องกันคลื่นแทรก
    else:
        pi.set_servo_pulsewidth(SERVO2_PIN, duty_us)

def set_motor(pwm_forward, pwm_backward, speed):
    speed = max(-100, min(100, speed))
    if speed >= 0:
        pwm_forward.ChangeDutyCycle(speed)
        pwm_backward.ChangeDutyCycle(0)
    else:
        pwm_forward.ChangeDutyCycle(0)
        pwm_backward.ChangeDutyCycle(-speed)

def set_motors(left_speed, right_speed):
    set_motor(pwm_in1, pwm_in2, left_speed)
    set_motor(pwm_in3, pwm_in4, right_speed)

# ---------------- Mapping จอยสติ๊ก PS4 ----------------
JS_DEVICE = "/dev/input/js0"
EVENT_FORMAT = "IhBB"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02
JS_EVENT_INIT = 0x80

AXIS_DPAD_X = 6
AXIS_DPAD_Y = 7
AXIS_L2 = 2
AXIS_R2 = 5

BUTTON_L1 = 4
BUTTON_R1 = 5

TRIGGER_THRESHOLD = 0.3

axis_state = {
    AXIS_DPAD_X: 0,
    AXIS_DPAD_Y: 0,
    AXIS_L2: -32767,
    AXIS_R2: -32767,
}

servo1_direction = 1
servo2_direction = 1
r1_prev_state = 0
l1_prev_state = 0
current_s1_state = "stop"
current_s2_state = "stop"

def normalize(raw_value):
    return raw_value / 32767.0

def dpad_direction(value):
    if value < -1000:
        return -1
    elif value > 1000:
        return 1
    return 0

def main():
    global current_s1_state, current_s2_state
    global servo1_direction, servo2_direction
    global r1_prev_state, l1_prev_state

    print(f"กำลังเปิดจอย: {JS_DEVICE}")
    try:
        jsdev = open(JS_DEVICE, "rb")
    except FileNotFoundError:
        print("ไม่พบจอยสติ๊ก! ตรวจสอบว่าเชื่อมต่อจอยแล้วหรือยัง")
        return
        
    print("เปิดจอยสำเร็จ กด Ctrl+C เพื่อหยุด")

    # ปิดสัญญาณ Servo เริ่มต้นให้นิ่งสนิท
    set_servo1(None)
    set_servo2(None)

    try:
        while True:
            evbuf = jsdev.read(EVENT_SIZE)
            if not evbuf:
                continue

            _time, value, ev_type, number = struct.unpack(EVENT_FORMAT, evbuf)
            ev_type_clean = ev_type & ~JS_EVENT_INIT

            # ปุ่มสลับทิศ R1 / L1
            if ev_type_clean == JS_EVENT_BUTTON:
                if number == BUTTON_R1:
                    if value == 1 and r1_prev_state == 0:
                        servo1_direction *= -1
                        print(f"\n[Servo 1] สลับทิศเป็น: {'ตามเข็ม' if servo1_direction == 1 else 'ทวนเข็ม'}")
                    r1_prev_state = value

                elif number == BUTTON_L1:
                    if value == 1 and l1_prev_state == 0:
                        servo2_direction *= -1
                        print(f"\n[Servo 2] สลับทิศเป็น: {'ตามเข็ม' if servo2_direction == 1 else 'ทวนเข็ม'}")
                    l1_prev_state = value

            # แกน D-pad, R2, L2
            if ev_type_clean == JS_EVENT_AXIS:
                if number in axis_state:
                    axis_state[number] = value

                # มอเตอร์ DC
                x = dpad_direction(axis_state[AXIS_DPAD_X])
                y = -dpad_direction(axis_state[AXIS_DPAD_Y])
                left_speed = (y * MOVE_SPEED) + (x * TURN_SPEED)
                right_speed = (y * MOVE_SPEED) - (x * TURN_SPEED)
                set_motors(left_speed, right_speed)

                # Servo 1 (R2): กดค้าง = หมุน, ปล่อย = ตัดไฟหยุดสนิท
                r2_amount = (normalize(axis_state[AXIS_R2]) + 1.0) / 2.0
                if r2_amount > TRIGGER_THRESHOLD:
                    s1_duty = SERVO_CW_US if servo1_direction == 1 else SERVO_CCW_US
                    s1_label = "หมุน"
                    set_servo1(s1_duty)
                else:
                    s1_label = "หยุด"
                    set_servo1(None)

                current_s1_state = s1_label

                # Servo 2 (L2): กดค้าง = หมุน, ปล่อย = ตัดไฟหยุดสนิท
                l2_amount = (normalize(axis_state[AXIS_L2]) + 1.0) / 2.0
                if l2_amount > TRIGGER_THRESHOLD:
                    s2_duty = SERVO_CW_US if servo2_direction == 1 else SERVO_CCW_US
                    s2_label = "หมุน"
                    set_servo2(s2_duty)
                else:
                    s2_label = "หยุด"
                    set_servo2(None)

                current_s2_state = s2_label

                print(
                    f"Motors L:{left_speed:+.0f}% R:{right_speed:+.0f}% | "
                    f"S1(R2):{current_s1_state} | S2(L2):{current_s2_state}   ",
                    end="\r",
                )

    except KeyboardInterrupt:
        print("\n\nกำลังหยุดระบบและคืนค่า GPIO...")

    finally:
        set_motors(0, 0)
        set_servo1(None)
        set_servo2(None)
        time.sleep(0.2)
        for p in (pwm_in1, pwm_in2, pwm_in3, pwm_in4):
            p.stop()
        GPIO.cleanup()
        pi.stop()
        jsdev.close()
        print("ปิดโปรแกรมเรียบร้อย")

if __name__ == "__main__":
    main()