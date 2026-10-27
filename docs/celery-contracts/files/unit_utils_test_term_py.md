### `t/unit/utils/test_term.py`

Portable guarantee: this file covers terminal colour and image-support helpers for CLI output, with no queue semantics. For sqlery, the relevant assurance is that CLI and log output formatting is safe for any input and degrades cleanly. Skipped on Windows.

**Colour output (CLI status text)**
- Ask for each of the 8 basic colours (black, red, green, yellow, blue, magenta, cyan, white); expect: output contains the matching escape code for that colour.
- Apply each style modifier (bold, underline, blink, reverse, bright, the intense colours, reset); expect: non-empty output, no error.

**Unicode and unexpected input**
- Colour non-ASCII text (accents, symbols) and join coloured pieces; expect: no encoding error, non-empty result.
- Strip colour from coloured text; expect: the plain original text.
- Take repr() of coloured text and of an empty colour object; expect: no error, empty one shows as empty quotes.
- Nest coloured pieces (red containing blue and green); expect: stripping colour still works, and mixing with plain unicode on either side does not raise.
- Append plain unicode text to a coloured string; expect: exact escape codes, text preserved, reset code between segments.
- Combine two coloured strings with unicode; expect: both keep their own colour codes and reset, in order.

**Reading files (image output)**
- Read a file of known bytes for inline display; expect: its exact base64 ASCII encoding.

**Environment detection**
- Interactive terminal and iTerm profile set; expect: images supported.
- Not an interactive terminal (piped/redirected), profile set; expect: images not supported.
- Interactive terminal but no iTerm profile; expect: images not supported.
