#!/usr/bin/env python3
"""
robot_control.py
- ควบคุมล้อขับเคลื่อน + Servo SG90
- ควบคุมมอเตอร์หมุนแปรง (ปุ่ม X = เริ่ม/หยุด, ปุ่ม △ = สลับทิศทาง)

แก้ไขจากเวอร์ชันก่อนหน้า:
  1. BRUSH_IN1/BRUSH_IN2 แก้กลับเป็น GPIO24/GPIO25 ให้ตรงกับสายที่ต่อจริง
     (เวอร์ชันก่อนตั้งเป็น GPIO20/21 ซึ่งไม่ตรงกับสายจริง มอเตอร์เลยไม่ขยับ)
  2. BUTTON_CROSS / BUTTON_TRIANGLE ยังเป็นค่าที่ "เดาไว้" (0 และ 2) ยังไม่ได้ยืนยัน
     ด้วย jstest จริง -- ถ้ากด X/△ แล้วไม่มีข้อความ [Brush] ขึ้นใน terminal
     ให้รัน `jstest /dev/input/js0` เช็คเลข Button จริง แล้วแก้ 2 ค่านี้ให้ตรง
  3. เพิ่ม debug print: กดปุ่มไหนก็ตามที่ยังไม่ผูกฟังก์ชัน จะขึ้น
     "[DEBUG] กดปุ่ม Button number = X" ช่วยหาเลขปุ่มจริงได้เร็วขึ้นโดยไม่ต้องเปิด jstest
"""

import struct
import time
import RPi.GPIO as GPIO
import pigpio

# ---------------- ตั้งค่า GPIO: มอเตอร์ล้อ ----------------
IN1, IN2 = 17, 27
IN3, IN4 = 22, 23
PWM_FREQ_MOTOR = 1000

MOVE_SPEED = 80
TURN_SPEED = 70

# ---------------- ตั้งค่า GPIO: มอเตอร์แปรง ----------------
# แก้กลับมาเป็น 24/25 ให้ตรงกับสายจริงที่ต่อไว้ (บอร์ดตัวที่ 2)
BRUSH_IN1 = 24  # ขา GPIO ฝั่งหมุนไปข้างหน้า
BRUSH_IN2 = 25  # ขา GPIO ฝั่งหมุนย้อนกลับ
BRUSH_SPEED = 50  # ความเร็วหมุนแปรง (0 - 100%)

# ---------------- ตั้งค่า GPIO: Servo 1 & 2 ----------------
SERVO1_PIN = 18
SERVO2_PIN = 19

SERVO_CW_US = 1600
SERVO_CCW_US = 1400

# ---------------- เริ่มต้น GPIO (สำหรับมอเตอร์ล้อและแปรง) ----------------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup([IN1, IN2, IN3, IN4, BRUSH_IN1, BRUSH_IN2], GPIO.OUT)

pwm_in1 = GPIO.PWM(IN1, PWM_FREQ_MOTOR)
pwm_in2 = GPIO.PWM(IN2, PWM_FREQ_MOTOR)
pwm_in3 = GPIO.PWM(IN3, PWM_FREQ_MOTOR)
pwm_in4 = GPIO.PWM(IN4, PWM_FREQ_MOTOR)

# PWM สำหรับมอเตอร์แปรง
pwm_brush1 = GPIO.PWM(BRUSH_IN1, PWM_FREQ_MOTOR)
pwm_brush2 = GPIO.PWM(BRUSH_IN2, PWM_FREQ_MOTOR)

for p in (pwm_in1, pwm_in2, pwm_in3, pwm_in4, pwm_brush1, pwm_brush2):
    p.start(0)

# ---------------- เริ่มต้น pigpio (สำหรับ Servo) ----------------
pi = pigpio.pi()
if not pi.connected:
    print("ไม่สามารถเชื่อมต่อ pigpiod ได้! กรุณารัน 'sudo pigpiod' ใน Terminal ก่อน")
    exit()

def set_servo1(duty_us):
    if duty_us is None:
        pi.set_servo_pulsewidth(SERVO1_PIN, 0)
        pi.write(SERVO1_PIN, 0)
    else:
        pi.set_servo_pulsewidth(SERVO1_PIN, duty_us)

def set_servo2(duty_us):
    if duty_us is None:
        pi.set_servo_pulsewidth(SERVO2_PIN, 0)
        pi.write(SERVO2_PIN, 0)
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

def set_brush(is_running, direction, speed=BRUSH_SPEED):
    """ฟังก์ชันสั่งมอเตอร์หมุนแปรง"""
    if not is_running:
        pwm_brush1.ChangeDutyCycle(0)
        pwm_brush2.ChangeDutyCycle(0)
    else:
        if direction == 1:
            pwm_brush1.ChangeDutyCycle(speed)
            pwm_brush2.ChangeDutyCycle(0)
        else:
            pwm_brush1.ChangeDutyCycle(0)
            pwm_brush2.ChangeDutyCycle(speed)

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

# ⚠️ ค่านี้ยังไม่ได้ยืนยันด้วย jstest จริง ถ้ากด X/△ แล้วไม่มีข้อความ [Brush] ขึ้น
# ให้รัน jstest /dev/input/js0 เช็คเลข Button จริงแล้วแก้ 2 ค่านี้
BUTTON_CROSS = 0     # ปุ่ม X (กดเริ่ม/หยุดแปรง)
BUTTON_TRIANGLE = 2  # ปุ่ม △ (กดสลับทิศทางแปรง)
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

# สถานะของแปรงหมุน
brush_running = False
brush_direction = 1
cross_prev_state = 0
triangle_prev_state = 0

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
    global brush_running, brush_direction
    global cross_prev_state, triangle_prev_state

    print(f"กำลังเปิดจอย: {JS_DEVICE}")
    try:
        jsdev = open(JS_DEVICE, "rb")
    except FileNotFoundError:
        print("ไม่พบจอยสติ๊ก! ตรวจสอบว่าเชื่อมต่อจอยแล้วหรือยัง")
        return
        
    print("เปิดจอยสำเร็จ กด Ctrl+C เพื่อหยุด")

    set_servo1(None)
    set_servo2(None)
    set_brush(False, 1)

    try:
        while True:
            evbuf = jsdev.read(EVENT_SIZE)
            if not evbuf:
                continue

            _time, value, ev_type, number = struct.unpack(EVENT_FORMAT, evbuf)
            ev_type_clean = ev_type & ~JS_EVENT_INIT

            # ---------------- จัดการปุ่มกด (Button) ----------------
            if ev_type_clean == JS_EVENT_BUTTON:
                # ปุ่ม R1: สลับทิศทาง Servo 1
                if number == BUTTON_R1:
                    if value == 1 and r1_prev_state == 0:
                        servo1_direction *= -1
                        print(f"\n[Servo 1] ทิศ: {'ตามเข็ม' if servo1_direction == 1 else 'ทวนเข็ม'}")
                    r1_prev_state = value

                # ปุ่ม L1: สลับทิศทาง Servo 2
                elif number == BUTTON_L1:
                    if value == 1 and l1_prev_state == 0:
                        servo2_direction *= -1
                        print(f"\n[Servo 2] ทิศ: {'ตามเข็ม' if servo2_direction == 1 else 'ทวนเข็ม'}")
                    l1_prev_state = value

                # ปุ่ม X: เปิด/ปิด การหมุนแปรง (Toggle ON/OFF)
                elif number == BUTTON_CROSS:
                    if value == 1 and cross_prev_state == 0:
                        brush_running = not brush_running
                        set_brush(brush_running, brush_direction)
                        print(f"\n[Brush] แปรงหมุน: {'ทำงาน (ON)' if brush_running else 'หยุด (OFF)'}")
                    cross_prev_state = value

                # ปุ่ม สามเหลี่ยม: สลับทิศทางการหมุนแปรง
                elif number == BUTTON_TRIANGLE:
                    if value == 1 and triangle_prev_state == 0:
                        brush_direction *= -1
                        set_brush(brush_running, brush_direction)
                        print(f"\n[Brush] สลับทิศทางเป็น: {'ทิศทาง A' if brush_direction == 1 else 'ทิศทาง B'}")
                    triangle_prev_state = value

                # 🔧 ชั่วคราว: print เลข Button ทุกปุ่มที่กด ช่วยหาค่าที่ถูกต้อง
                # ลบทิ้งได้หลังยืนยันเลข Button ครบแล้ว
                else:
                    if value == 1:
                        print(f"\n[DEBUG] กดปุ่ม Button number = {number} (ยังไม่ได้ผูกกับฟังก์ชันใด)")

            # ---------------- จัดการแกน (Axis: มอเตอร์ล้อ & Servo) ----------------
            if ev_type_clean == JS_EVENT_AXIS:
                if number in axis_state:
                    axis_state[number] = value

                # มอเตอร์ล้อ (ใช้สมการที่คุณปรับแก้แล้ว)
                x = dpad_direction(axis_state[AXIS_DPAD_X])
                y = -dpad_direction(axis_state[AXIS_DPAD_Y])
                
                left_speed = (y * MOVE_SPEED) + (x * TURN_SPEED)
                right_speed = (y * MOVE_SPEED) - (x * TURN_SPEED)
                set_motors(left_speed, right_speed)

                # Servo 1 (R2)
                r2_amount = (normalize(axis_state[AXIS_R2]) + 1.0) / 2.0
                if r2_amount > TRIGGER_THRESHOLD:
                    s1_duty = SERVO_CW_US if servo1_direction == 1 else SERVO_CCW_US
                    s1_label = "หมุน"
                    set_servo1(s1_duty)
                else:
                    s1_label = "หยุด"
                    set_servo1(None)

                current_s1_state = s1_label

                # Servo 2 (L2)
                l2_amount = (normalize(axis_state[AXIS_L2]) + 1.0) / 2.0
                if l2_amount > TRIGGER_THRESHOLD:
                    s2_duty = SERVO_CW_US if servo2_direction == 1 else SERVO_CCW_US
                    s2_label = "หมุน"
                    set_servo2(s2_duty)
                else:
                    s2_label = "หยุด"
                    set_servo2(None)

                current_s2_state = s2_label

    except KeyboardInterrupt:
        print("\n\nกำลังหยุดระบบและคืนค่า GPIO...")

    finally:
        set_motors(0, 0)
        set_brush(False, 1)  # สั่งหยุดแปรงหมุน
        set_servo1(None)
        set_servo2(None)
        time.sleep(0.2)
        
        for p in (pwm_in1, pwm_in2, pwm_in3, pwm_in4, pwm_brush1, pwm_brush2):
            p.stop()
            
        GPIO.cleanup()
        pi.stop()
        jsdev.close()
        print("ปิดโปรแกรมเรียบร้อย")

if __name__ == "__main__":
    main()