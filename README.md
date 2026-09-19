# Pico Room Monitor

Raspberry Pi Pico 2 W room monitor: temperature + humidity on an I2C LCD,
a NeoPixel "temperature bar", phone notifications via ntfy, minute-by-minute
CSV logging, a night-mode button, and over-the-air updates from this repo.

## Hardware
- Raspberry Pi Pico 2 W (WiFi)
- DHT20 temperature/humidity sensor (I2C, addr 0x38)
- 16x2 I2C LCD (addr 0x27)
- NeoPixel strand (15 LEDs) or ring (12 LEDs) on GP2

## Files on the Pico
- `main.py` ............ the program (updated via OTA)
- `secrets.py` ......... WiFi + ntfy topic (NOT in this repo - see below)
- `lcd_api.py` ......... LCD driver
- `pico_i2c_lcd.py` .... LCD I2C driver
- `dht20.py` ........... sensor driver
- `lcd_flip_helpers.py` . software 180-degree character flip, for when the
  LCD is physically mounted upside down (pulled by OTA `update` alongside
  `main.py`)

## Setup
1. Flash MicroPython for the **Pico 2 W**.
2. Copy all the files above onto the Pico.
3. Create `secrets.py` on the Pico from the template and fill in your details.
4. Edit `GITHUB_USER` / `GITHUB_REPO` at the top of `main.py`.
5. Set `LCD_FLIPPED` in `main.py` to match how the LCD is physically
   mounted (`True` if upside down).
6. Save `main.py` and let it run.

### LCD mounted upside down
When `LCD_FLIPPED = True`, the reading is drawn with `lcd_flip_helpers.py`'s
software character flip, in a compact `T25C H66%` format on one line -
that module's font only covers digits, `.`, `:`, `-`, `%`, `C`, `T`, `H`, so
it can't render the full "Temp:"/"Humidity:" labels or the "Connecting
WiFi" boot message (which stays unflipped and upside-down for those few
seconds at startup). Only 8 custom-character slots exist on the LCD
controller, which is why both readings share one compact line instead of
one line each - splitting them across both physical rows risks the two
lines fighting over those 8 slots and corrupting each other's digits.

## Controls
- **Onboard BOOTSEL button**: toggles night mode (LED + LCD off).
- **ntfy app**: send any message to get an instant reading.
- **ntfy app**: send `update` to pull the latest `main.py` from this repo and reboot.

## Reliability
`main.py` runs an 8-second hardware watchdog (`machine.WDT`). If WiFi setup
ever wedges after a reset (a known Pico W quirk where `wlan.active()` /
`wlan.connect()` can hang at the driver level, below what a `try/except` can
catch), the watchdog force-resets the board instead of leaving it frozen.

## Logging
Readings are appended every minute to `templog.csv` on the Pico
(`timestamp,temp_c,humidity_pct`). Download it via Thonny.
