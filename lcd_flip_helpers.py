# lcd_flip_helpers.py
# Software 180-degree flip for HD44780 character LCDs (digits + a few symbols)
#
# The HD44780 has NO native rotate command - characters come from a fixed ROM
# font that can't be addressed upside down. To make text appear right-side-up
# when the LCD module itself is physically mounted upside down, this module:
#
#   1) Builds mirrored (180-degree rotated) bitmaps for the characters we
#      need, and loads them into CGRAM (the controller's 8 custom-character
#      slots, code points 0-7).
#   2) Writes those custom glyphs in REVERSED column order per row, so the
#      whole line lands correctly once the physical LCD is upside down.
#
# HARD LIMIT: only 8 distinct custom glyphs can be loaded at once. This
# module tracks which glyphs are currently needed for a given line and
# raises an error if a line needs more than 8 unique characters - so keep
# labels short (e.g. "T" / "H" rather than "Temp:" / "Humidity:") if you
# want them to share the budget with digits.
#
# CGRAM SLOTS ARE CACHED, NOT RELOADED EVERY CALL: this project's
# lcd_api.py/pico_i2c_lcd.py calls gc.collect() after every single I2C
# transaction, and custom_char() does 9 of those (1 command + 8 data bytes)
# per glyph. Reloading a whole line's worth of glyphs (e.g. 7) on every
# refresh means ~60+ forced garbage collections back to back, which can
# block long enough to disrupt other time-sensitive things (like WiFi)
# running on the same core. So a glyph already sitting in a slot from the
# previous call is left alone - only characters that aren't currently
# loaded anywhere get a fresh custom_char() call. Static characters (a
# label's letters, units) end up loaded once and never touched again;
# only the digits that actually change between calls cost anything.
#
# Requires your lcd_api.py to expose:
#   lcd.custom_char(location, charmap)   # loads an 8-byte bitmap into CGRAM slot 0-7
#   lcd.putchar(ch)                      # writes one raw character/byte
#   lcd.move_to(col, row)
# (These are present in the common dhylands/python_lcd lcd_api.py used with
# pico_i2c_lcd.py - the same library this project already uses.)

# Standard 5x8 HD44780 font bitmaps, 5 active bits per byte (rows top->bottom)
_FONT = {
    '0': [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E, 0x00],
    '1': [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E, 0x00],
    '2': [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F, 0x00],
    '3': [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E, 0x00],
    '4': [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02, 0x00],
    '5': [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E, 0x00],
    '6': [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E, 0x00],
    '7': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08, 0x00],
    '8': [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E, 0x00],
    '9': [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C, 0x00],
    '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C, 0x00],
    ':': [0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00, 0x00],
    '%': [0x19, 0x1A, 0x04, 0x04, 0x08, 0x0B, 0x13, 0x00],
    'C': [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E, 0x00],
    'T': [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x00],
    'H': [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11, 0x00],
    '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00, 0x00],
    ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
}

_NUM_SLOTS = 8


def _flip_glyph(bitmap):
    """180-degree rotate a 5x8 glyph: reverse row order top<->bottom, and
    reverse the 5 active bits within each row (left<->right)."""
    flipped = []
    for row in reversed(bitmap):
        mirrored = 0
        for bit in range(5):
            if row & (1 << bit):
                mirrored |= (1 << (4 - bit))
        flipped.append(mirrored)
    return flipped


class FlippedLcdWriter:
    """
    Manages the LCD's 8 CGRAM slots across calls, reloading a glyph only
    when it isn't already sitting in a slot - see the module docstring for
    why that matters on this hardware. Writes flipped glyphs in reverse
    column order per row, so the row reads correctly once the physical LCD
    is mounted upside down.
    """

    def __init__(self, lcd, num_cols=16):
        self.lcd = lcd
        self.num_cols = num_cols
        self._slot_map = {}  # char -> CGRAM slot, persists across calls

    def _ensure_slots_loaded(self, chars_needed):
        needed = set(chars_needed)

        # Free up slots held by characters this line no longer needs -
        # anything still needed stays exactly where it is (no reload).
        for ch in list(self._slot_map):
            if ch not in needed:
                del self._slot_map[ch]

        used_slots = set(self._slot_map.values())
        free_slots = [s for s in range(_NUM_SLOTS) if s not in used_slots]

        for ch in chars_needed:
            if ch not in self._slot_map:
                slot = free_slots.pop(0)
                self.lcd.custom_char(slot, bytearray(_flip_glyph(_FONT[ch])))
                self._slot_map[ch] = slot

    def write_flipped_row(self, text, physical_row):
        """
        text: the upright string you want a viewer to read, e.g. 'T 23.4C'
        physical_row: which physical LCD row (0 or 1) to draw on. Since the
        module is mounted upside down, what you consider your "top" logical
        row should be sent with physical_row=1, and your "bottom" logical
        row with physical_row=0 - swap them here, not in your own code.
        """
        text = text[:self.num_cols].ljust(self.num_cols)

        unique_chars = []
        for ch in text:
            if ch != ' ' and ch not in unique_chars:
                if ch not in _FONT:
                    raise ValueError("No flipped glyph defined for %r" % ch)
                unique_chars.append(ch)

        if len(unique_chars) > _NUM_SLOTS:
            raise ValueError(
                "Row needs %d distinct glyphs (%s) but only %d CGRAM slots "
                "are available - shorten labels or reduce precision"
                % (len(unique_chars), unique_chars, _NUM_SLOTS)
            )

        self._ensure_slots_loaded(unique_chars)

        reversed_text = text[::-1]
        self.lcd.move_to(0, physical_row)
        for ch in reversed_text:
            if ch == ' ':
                self.lcd.putstr(' ')
            else:
                self.lcd.putchar(chr(self._slot_map[ch]))
