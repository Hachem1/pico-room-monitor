# Pico Room Monitor

Raspberry Pi Pico 2 W room monitor: temperature + humidity on an I2C LCD,
a NeoPixel "temperature bar", phone notifications via ntfy, minute-by-minute
CSV logging, a night-mode button, and over-the-air updates from this repo.

## Hardware
- Raspberry Pi Pico 2 W (WiFi)
- DHT20 temperature/humidity sensor (I2C, addr 0x38)
- 16x2 I2C LCD (addr 0x27)
- NeoPixel strand (15 LEDs) or ring (12 LEDs) on GP2
- LM393 comparator thermistor module ("THERM1"): `A0` -> GP26 (ADC0),
  `D0` -> GP16, `+` -> 3V3(OUT), `G` -> GND
- Bare 3-pin thermistor breakout ("THERM2"): `S` -> GP27 (ADC1),
  `+` -> 3V3(OUT), `-` -> GND

Both thermistor readings are approximate (Beta-equation math with typical
10k-module constants baked into `main.py` - see the comments above
`NTC_BETA` if your readings drift from the DHT20 reference and need
recalibrating).

## Files on the Pico
- `main.py` ............ the program (updated via OTA)
- `secrets.py` ......... WiFi + ntfy topic (NOT in this repo - see below)
- `lcd_api.py` ......... LCD driver
- `pico_i2c_lcd.py` .... LCD I2C driver
- `dht20.py` ........... sensor driver
- `lcd_flip_helpers.py` . optional helper for a physically upside-down LCD
  (not wired into `main.py` - only needed if you mount the LCD flipped)

## Setup
1. Flash MicroPython for the **Pico 2 W**.
2. Copy all the files above onto the Pico.
3. Create `secrets.py` on the Pico from the template and fill in your details.
4. Edit `GITHUB_USER` / `GITHUB_REPO` at the top of `main.py`.
5. Save `main.py` and let it run.

## Controls
- **Onboard BOOTSEL button**: toggles night mode (LED + LCD off).
- **ntfy app**: send any message to get an instant reading.
- **ntfy app**: send `update` to pull the latest `main.py` from this repo and reboot.
- **LCD**: cycles every 4 seconds between the DHT20 page (Temp/Humidity)
  and the thermistor page (T1/T2, plus `HOT`/`ok` for the LM393 board's
  digital threshold output).
- **Thermistor alert**: an ntfy notification fires the moment the LM393
  board's `D0` output trips its threshold (adjust the trip point with the
  board's blue trimmer pot).

## Logging
Readings are appended every minute to `templog.csv` on the Pico
(`timestamp,temp_c,humidity_pct,therm1_c,therm2_c`). Download it via Thonny.
Note: if you already have a `templog.csv` on the Pico from before, its
header only has 3 columns - either delete it so a fresh 5-column header
gets written, or just know its older rows will look short next to newer
ones.
