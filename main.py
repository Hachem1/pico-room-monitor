from machine import I2C, Pin
from lcd_api import LcdApi
from pico_i2c_lcd import I2cLcd
from dht20 import DHT20
from neopixel import NeoPixel
import network
import machine
import time
import json
import gc
import os
import rp2

# Credentials live in secrets.py ON THE PICO (not in this repo)
from secrets import WIFI_SSID, WIFI_PASSWORD, NTFY_TOPIC

try:
    import urequests as requests
except ImportError:
    import requests

try:
    import ntptime
except ImportError:
    ntptime = None

# ---------------- CONFIG ----------------
NOTIFY_EVERY_MINUTES = 30
POLL_EVERY_SECONDS = 15

# Logging
LOG_FILE = "templog.csv"
LOG_EVERY_SECONDS = 60               # log a reading once a minute
TZ_OFFSET_HOURS = 1                  # London: 1 in summer (BST), 0 in winter (GMT)

# OTA - point these at YOUR public GitHub repo
GITHUB_USER = "YOUR_GITHUB_USERNAME"
GITHUB_REPO = "pico-room-monitor"
GITHUB_BRANCH = "main"
OTA_FILES = ["main.py"]              # files to pull when you send "update"
# ----------------------------------------


NTFY_URL = "https://ntfy.sh/" + NTFY_TOPIC
BOT_TITLES = ("Room conditions", "Reading (on request)", "Pico")

# --- LCD / sensor I2C config ---
SDA = 14
SCL = 15
I2C_BUS = 1
LCD_ADDR = 0x27
TEMP_ADDR = 0x38
LCD_NUM_ROWS = 2
LCD_NUM_COLS = 16

# --- LED config ---
LED_PIN = 2
LED_COUNT = 15        # 15 for the strand, 12 for the ring
TEMP_MIN = 13
TEMP_MAX = 27

# --- Set up hardware ---
i2c = I2C(I2C_BUS, sda=Pin(SDA), scl=Pin(SCL), freq=400000)
lcd = I2cLcd(i2c, LCD_ADDR, LCD_NUM_ROWS, LCD_NUM_COLS)
dht20 = DHT20(TEMP_ADDR, i2c)
strand = NeoPixel(Pin(LED_PIN), LED_COUNT)
wlan = network.WLAN(network.STA_IF)

# --- State ---
night_mode = False
last_btn = 0
last_toggle = 0
last_cmd_time = 0
primed = False


def connect_wifi():
    wlan.active(True)
    if wlan.isconnected():
        return True
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(20):
        if wlan.isconnected():
            print("WiFi connected:", wlan.ifconfig()[0])
            return True
        time.sleep(1)
    print("WiFi connection failed")
    return False


def sync_time():
    if ntptime is None:
        print("ntptime not available - timestamps may be wrong")
        return
    try:
        ntptime.settime()
        print("Clock synced over the internet")
    except Exception as e:
        print("NTP sync failed:", e)


def timestamp():
    t = time.localtime(time.time() + TZ_OFFSET_HOURS * 3600)
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        t[0], t[1], t[2], t[3], t[4], t[5])


def ensure_log_header():
    try:
        open(LOG_FILE, "r").close()
    except OSError:
        with open(LOG_FILE, "w") as f:
            f.write("timestamp,temp_c,humidity_pct\n")


def log_reading(temp, humidity):
    line = "{},{:.1f},{:.1f}\n".format(timestamp(), temp, humidity)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
        print("Logged:", line.strip())
    except Exception as e:
        print("Log failed:", e)


def notify_text(text, title="Pico"):
    try:
        if not wlan.isconnected():
            connect_wifi()
        r = requests.post(NTFY_URL, data=text, headers={"Title": title})
        r.close()
    except Exception as e:
        print("Notify failed:", e)
    gc.collect()


def publish_reading(temp, humidity, title="Room conditions"):
    notify_text("Temp: {}C   Humidity: {}%".format(temp, humidity), title)


def get_new_command():
    global last_cmd_time, primed
    url = NTFY_URL + "/json?poll=1&since=" + str(POLL_EVERY_SECONDS + 10) + "s"
    try:
        r = requests.get(url)
        body = r.text
        r.close()
    except Exception as e:
        print("Poll failed:", e)
        gc.collect()
        return None
    gc.collect()

    newest = last_cmd_time
    cmd_text = None
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if msg.get("event") != "message":
            continue
        if msg.get("title", "") in BOT_TITLES:
            continue
        t = msg.get("time", 0)
        if t > newest:
            newest = t
            cmd_text = msg.get("message", "").strip().lower()

    result = cmd_text if (newest > last_cmd_time and primed) else None
    last_cmd_time = newest
    primed = True
    return result


def ota_update():
    base = "https://raw.githubusercontent.com/{}/{}/{}/".format(
        GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH)
    for fn in OTA_FILES:
        try:
            r = requests.get(base + fn)
            code = r.status_code
            text = r.text if code == 200 else ""
            r.close()
        except Exception as e:
            print("OTA download error:", fn, e)
            return False
        gc.collect()
        if code != 200 or len(text) == 0:
            print("OTA bad download:", fn, code)
            return False
        try:
            with open(fn + ".new", "w") as f:
                f.write(text)
        except Exception as e:
            print("OTA write error:", fn, e)
            return False
    for fn in OTA_FILES:
        try:
            try:
                os.remove(fn + ".bak")
            except OSError:
                pass
            try:
                os.rename(fn, fn + ".bak")
            except OSError:
                pass
            os.rename(fn + ".new", fn)
            print("Updated:", fn)
        except Exception as e:
            print("OTA swap error:", fn, e)
            return False
    return True


def temp_to_index(temp):
    fraction = (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)
    index = round(fraction * (LED_COUNT - 1))
    if index < 0:
        index = 0
    if index > LED_COUNT - 1:
        index = LED_COUNT - 1
    return index


def index_to_colour(index):
    third = LED_COUNT / 3
    if index < third:
        return (0, 0, 40)
    elif index < third * 2:
        return (0, 40, 0)
    else:
        return (40, 0, 0)


def draw_labels():
    lcd.clear()
    lcd.putstr("Temp:")
    lcd.move_to(0, 1)
    lcd.putstr("Humidity:")


def enter_night_mode():
    strand.fill((0, 0, 0))
    strand.write()
    lcd.clear()
    lcd.display_off()
    lcd.backlight_off()
    print("Night mode ON")


def exit_night_mode():
    lcd.backlight_on()
    lcd.display_on()
    draw_labels()
    print("Night mode OFF")


def check_button():
    global last_btn, last_toggle, night_mode
    val = rp2.bootsel_button()
    now_ms = time.ticks_ms()
    if last_btn == 0 and val == 1:
        if time.ticks_diff(now_ms, last_toggle) > 300:
            last_toggle = now_ms
            night_mode = not night_mode
            if night_mode:
                enter_night_mode()
            else:
                exit_night_mode()
    last_btn = val


def responsive_wait(ms):
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < ms:
        check_button()
        time.sleep_ms(20)


# --- Start up ---
lcd.clear()
lcd.putstr("Connecting WiFi")
connect_wifi()
sync_time()
ensure_log_header()
draw_labels()

last_notify = 0
last_poll = 0
last_log = 0

while True:

    measurements = dht20.measurements
    temp = measurements['t']
    humidity = measurements['rh']

    if not night_mode:
        lcd.move_to(10, 0)
        lcd.putstr(f"{temp:.1f} ")
        lcd.move_to(10, 1)
        lcd.putstr(f"{humidity:.1f} ")
        index = temp_to_index(temp)
        strand.fill((0, 0, 0))
        strand[index] = index_to_colour(index)
        strand.write()

    now = time.time()

    if now - last_log >= LOG_EVERY_SECONDS:
        log_reading(round(temp, 1), round(humidity, 1))
        last_log = now

    if now - last_notify >= NOTIFY_EVERY_MINUTES * 60:
        publish_reading(round(temp, 1), round(humidity, 1))
        last_notify = now

    if now - last_poll >= POLL_EVERY_SECONDS:
        last_poll = now
        cmd = get_new_command()
        if cmd is not None:
            if "update" in cmd:
                notify_text("Updating from GitHub...")
                if ota_update():
                    notify_text("Update OK - rebooting")
                    time.sleep(1)
                    machine.reset()
                else:
                    notify_text("Update failed - still running old version")
            else:
                publish_reading(round(temp, 1), round(humidity, 1),
                                title="Reading (on request)")

    responsive_wait(2000)
