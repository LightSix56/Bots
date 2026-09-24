"""Тестовый пакет. Настраивает UTF-8 вывод для прогонов на Windows."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import console  # noqa: E402

console.enable_utf8()
