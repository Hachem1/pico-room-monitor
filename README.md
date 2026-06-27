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

## Logging
Readings are appended every minute to `templog.csv` on the Pico
(`timestamp,temp_c,humidity_pct`). Download it via Thonny.
