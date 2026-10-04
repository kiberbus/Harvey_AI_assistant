"""Модули Харви. Точка входа — harvey.py в корне проекта."""

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")   # torch (Silero) + ctranslate2 (Whisper) в одном процессе
