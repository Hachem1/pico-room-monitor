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
    ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
}


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
    Dynamically manages the LCD's 8 CGRAM slots: for each line you want to
    draw, it loads whichever flipped glyphs that line actually needs (up to
    8 unique characters), then writes them in reverse order so the row reads
    correctly once the physical LCD is mounted upside down.
    """

    def __init__(self, lcd, num_cols=16):
        self.lcd = lcd
        self.num_cols = num_cols
        self._slot_map = {}

    def _assign_slots(self, chars_needed):
        self._slot_map = {}
        for slot, ch in enumerate(chars_needed):
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

        if len(unique_chars) > 8:
            raise ValueError(
                "Row needs %d distinct glyphs (%s) but only 8 CGRAM slots "
                "are available - shorten labels or reduce precision"
                % (len(unique_chars), unique_chars)
            )

        self._assign_slots(unique_chars)

        reversed_text = text[::-1]
        self.lcd.move_to(0, physical_row)
        for ch in reversed_text:
            if ch == ' ':
                self.lcd.putstr(' ')
            else:
                self.lcd.putchar(chr(self._slot_map[ch]))
