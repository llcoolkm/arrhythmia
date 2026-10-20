# LED map (5706, measured 2026-10-16)

Decoded on a unit running an early Arrhythmia development build (label 6003), filmed with a camera.
`tools/ledscan.py` lit each of the 224 LED-buffer bytes alone (command 03, level 16) for 0.5 s while the camera
filmed; every byte was assigned to the light and colour channel it lit. Machine-readable copy: `led-map.json`.

**Result (high confidence):** 69 RGB lights x 3 channels = 207 bytes; the other 17 bytes drive nothing
(6, 7, 13, 14, 15, 62, 63, 69, 70, 71, 118, 119, 125, 126, 127, 182, 183). Value per byte is brightness 0..0xFE (command 03 sends v << 1).

Verified by drawing a known pattern through the map (pad rows red / green / blue / white, all buttons green, all
knob lights red); the photo matched exactly: `img/led-map-verify.jpg`.

Structure: the buffer is four blocks of 56 bytes (one per LED driver chain, probably). Most lights use three
consecutive bytes in R, G, B order, but about a quarter do not: in those the red byte sits 14 or 15 bytes away from G
and B (e.g. pad row 0 col 3: R 103, G 88, B 89). Always go through the table, never compute an index.

## Pads (R/G/B byte indices; row 0 is the top row, col 0 the left)

| | col 0 | col 1 | col 2 | col 3 | col 4 | col 5 | col 6 | col 7 |
|---|---|---|---|---|---|---|---|---|
| row 0 | 37/38/39 | 24/25/26 | 100/101/102 | 103/88/89 | 156/157/158 | 159/144/145 | 202/203/204 | 205/206/207 |
| row 1 | 30/31/16 | 90/91/92 | 93/94/95 | 80/81/82 | 146/147/148 | 149/150/151 | 136/137/138 | 195/196/197 |
| row 2 | 20/21/22 | 83/84/85 | 86/87/72 | 73/74/75 | 139/140/141 | 142/143/128 | 129/130/131 | 185/186/187 |
| row 3 | 10/11/12 | 76/77/78 | 79/64/65 | 66/67/68 | 132/133/134 | 135/120/121 | 122/123/124 | 191/176/177 |

## Buttons and knob lights

Knob lights are named by the macro number printed under the knob (top row 2 Start, 4 Slope, 6 HP Filter,
8 Resonance; bottom row 1 Tune, 3 Length, 5 Distortion, 7 LP Filter, then Master Filter). Master Volume has no
light. Buttons are named by their main label.

| Light | R | G | B |
|---|---|---|---|
| macro1 | 0 | 1 | 2 |
| macro2 | 3 | 4 | 5 |
| btn_PatternSettings | 23 | 8 | 9 |
| btn_Gate | 17 | 18 | 19 |
| btn_Velocity | 27 | 28 | 29 |
| btn_2 | 47 | 32 | 33 |
| btn_Note | 34 | 35 | 36 |
| btn_Up | 54 | 55 | 40 |
| btn_Sample | 41 | 42 | 43 |
| btn_1 | 44 | 45 | 46 |
| btn_SampleRec | 48 | 49 | 50 |
| btn_Down | 51 | 52 | 53 |
| macro3 | 56 | 57 | 58 |
| macro4 | 59 | 60 | 61 |
| btn_3 | 110 | 111 | 96 |
| btn_4 | 97 | 98 | 99 |
| btn_1-16 | 104 | 105 | 106 |
| btn_Tempo | 107 | 108 | 109 |
| macro5 | 112 | 113 | 114 |
| macro6 | 115 | 116 | 117 |
| btn_5 | 166 | 167 | 152 |
| btn_6 | 153 | 154 | 155 |
| btn_Clear | 160 | 161 | 162 |
| btn_Duplicate | 163 | 164 | 165 |
| macro7 | 168 | 169 | 170 |
| macro8 | 171 | 172 | 173 |
| masterfilter | 174 | 175 | 181 |
| btn_Play | 178 | 179 | 180 |
| btn_FX | 198 | 199 | 184 |
| btn_Rec | 188 | 189 | 190 |
| btn_Mixer | 192 | 193 | 194 |
| btn_Patterns | 215 | 200 | 201 |
| btn_Shift | 222 | 223 | 208 |
| btn_7 | 209 | 210 | 211 |
| btn_8 | 212 | 213 | 214 |
| btn_Save | 216 | 217 | 218 |
| btn_Projects | 219 | 220 | 221 |
