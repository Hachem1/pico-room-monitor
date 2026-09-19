from machine import I2C, Pin, ADC, WDT
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
import math

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
GITHUB_USER = "Hachem1"
GITHUB_REPO = "pico-room-monitor"
GITHUB_BRANCH = "main"
OTA_FILES = ["main.py"]              # files to pull when you send "update"
# ----------------------------------------


NTFY_URL = "https://ntfy.sh/" + NTFY_TOPIC
BOT_TITLES = ("Room conditions", "Reading (on request)", "Pico", "Thermistor alert")

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
TEMP_MIN = 18
TEMP_MAX = 32

# --- Extra thermistor sensors ---
# THERM1 = the LM393 comparator board (A0 = analog voltage, D0 = digital
# threshold output flipped by the blue trimmer pot).
# THERM2 = the bare 3-pin thermistor breakout (S = analog voltage only).
THERM1_ADC_PIN = 26          # A0 -> GP26 / ADC0
THERM1_DIGITAL_PIN = 16      # D0 -> GP16
THERM2_ADC_PIN = 27          # S  -> GP27 / ADC1

# NTC voltage-divider math. These are typical values for these common 10k
# modules (fixed resistor on the VCC side, thermistor to GND, signal tapped
# at the midpoint) - if readings come out backwards (falling as the room
# warms up) or way off vs. the DHT20, that means your board is wired the
# other way round or uses different part values: try flipping
# THERM_ON_HIGH_SIDE below, or adjust NTC_R_SERIES/NTC_R25 to match a bench
# reading against the DHT20.
NTC_BETA = 3950
NTC_R25_OHMS = 10000
NTC_R_SERIES_OHMS = 10000
NTC_T25_KELVIN = 298.15
THERM_ON_HIGH_SIDE = False
ADC_VREF = 3.3

# D0 alert: fires an ntfy notification the moment the comparator trips.
# Flip this if the alert fires when it shouldn't (or never fires) - it
# depends on which way round your board's comparator is wired.
THERM1_ALERT_ACTIVE_HIGH = True

DISPLAY_CYCLE_SECONDS = 4    # how long each LCD page is shown before swapping

# --- Set up hardware ---
i2c = I2C(I2C_BUS, sda=Pin(SDA), scl=Pin(SCL), freq=400000)
lcd = I2cLcd(i2c, LCD_ADDR, LCD_NUM_ROWS, LCD_NUM_COLS)
dht20 = DHT20(TEMP_ADDR, i2c)
strand = NeoPixel(Pin(LED_PIN), LED_COUNT)
wlan = network.WLAN(network.STA_IF)

therm1_adc = ADC(THERM1_ADC_PIN)
therm1_digital = Pin(THERM1_DIGITAL_PIN, Pin.IN)
therm2_adc = ADC(THERM2_ADC_PIN)

# Hardware watchdog: if the WiFi chip wedges after a reset (a known Pico W
# quirk - it can hang inside wlan.active()/wlan.connect() at the driver
# level, below anything a try/except can catch) or anything else stalls the
# loop, this forces a full hard reset instead of freezing forever. 8000ms is
# close to the RP2040/2350 hardware maximum - call wdt.feed() often, from
# anywhere that might block for a while, or it'll reset during normal use.
wdt = WDT(timeout=8000)

# --- State ---
night_mode = False
last_btn = 0
last_toggle = 0
last_cmd_time = 0
primed = False

PAGE_DHT20 = 0
PAGE_THERM = 1
current_page = PAGE_DHT20
last_page_switch = 0
last_therm_alert_active = False


def connect_wifi():
    wdt.feed()
    wlan.active(True)
    if wlan.isconnected():
        return True
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(20):
        wdt.feed()
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
            f.write("timestamp,temp_c,humidity_pct,therm1_c,therm2_c\n")


def _fmt_temp(temp):
    return "{:.1f}".format(temp) if temp is not None else "NA"


def log_reading(temp, humidity, therm1=None, therm2=None):
    line = "{},{:.1f},{:.1f},{},{}\n".format(
        timestamp(), temp, humidity, _fmt_temp(therm1), _fmt_temp(therm2))
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


def read_ntc_temp_c(adc):
    """Convert one of the NTC thermistor boards' analog reading to Celsius.
    Returns None right at the voltage rails, where the divider math blows up."""
    raw = adc.read_u16()
    voltage = raw / 65535 * ADC_VREF
    if voltage <= 0.02 or voltage >= ADC_VREF - 0.02:
        return None
    if THERM_ON_HIGH_SIDE:
        r_therm = NTC_R_SERIES_OHMS * voltage / (ADC_VREF - voltage)
    else:
        r_therm = NTC_R_SERIES_OHMS * (ADC_VREF - voltage) / voltage
    inv_t = 1.0 / NTC_T25_KELVIN + (1.0 / NTC_BETA) * math.log(r_therm / NTC_R25_OHMS)
    return (1.0 / inv_t) - 273.15


def check_therm_alert(digital_value):
    global last_therm_alert_active
    tripped = bool(digital_value) if THERM1_ALERT_ACTIVE_HIGH else not bool(digital_value)
    if tripped and not last_therm_alert_active:
        notify_text("Thermistor threshold tripped (D0)", title="Thermistor alert")
    last_therm_alert_active = tripped


def draw_dht20_labels():
    lcd.clear()
    lcd.putstr("Temp:")
    lcd.move_to(0, 1)
    lcd.putstr("Humidity:")


def draw_therm_labels():
    lcd.clear()
    lcd.putstr("T1:")
    lcd.move_to(0, 1)
    lcd.putstr("T2:")


def enter_night_mode():
    strand.fill((0, 0, 0))
    strand.write()
    lcd.clear()
    lcd.display_off()
    lcd.backlight_off()
    print("Night mode ON")


def exit_night_mode():
    global current_page, last_page_switch
    lcd.backlight_on()
    lcd.display_on()
    current_page = PAGE_DHT20
    last_page_switch = time.time()
    draw_dht20_labels()
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
        wdt.feed()
        check_button()
        time.sleep_ms(20)


# --- Start up ---
lcd.clear()
lcd.putstr("Connecting WiFi")
connect_wifi()
sync_time()
ensure_log_header()
draw_dht20_labels()

last_notify = 0
last_poll = 0
last_log = 0
last_page_switch = time.time()

while True:

    wdt.feed()

    measurements = dht20.measurements
    temp = measurements['t']
    humidity = measurements['rh']

    therm1_temp = read_ntc_temp_c(therm1_adc)
    therm2_temp = read_ntc_temp_c(therm2_adc)
    check_therm_alert(therm1_digital.value())

    now = time.time()

    if not night_mode:
        if now - last_page_switch >= DISPLAY_CYCLE_SECONDS:
            last_page_switch = now
            current_page = PAGE_THERM if current_page == PAGE_DHT20 else PAGE_DHT20
            if current_page == PAGE_DHT20:
                draw_dht20_labels()
            else:
                draw_therm_labels()

        if current_page == PAGE_DHT20:
            lcd.move_to(10, 0)
            lcd.putstr(f"{temp:.1f} ")
            lcd.move_to(10, 1)
            lcd.putstr(f"{humidity:.1f} ")
        else:
            lcd.move_to(3, 0)
            lcd.putstr(_fmt_temp(therm1_temp) + "C ")
            lcd.move_to(10, 0)
            lcd.putstr("HOT" if therm1_digital.value() else "ok ")
            lcd.move_to(3, 1)
            lcd.putstr(_fmt_temp(therm2_temp) + "C ")

        index = temp_to_index(temp)
        strand.fill((0, 0, 0))
        strand[index] = index_to_colour(index)
        strand.write()

    if now - last_log >= LOG_EVERY_SECONDS:
        log_reading(round(temp, 1), round(humidity, 1), therm1_temp, therm2_temp)
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
