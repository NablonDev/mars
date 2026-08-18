from __future__ import annotations

import sys
from pathlib import Path

import azure.functions as func

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from azure_functions.enqueue_new_mail import bp as enqueue_new_mail_bp

app = func.FunctionApp()

print("=" * 80)
print("FUNCTION APP FILE:")
print(__file__)
print("=" * 80)

app.register_functions(enqueue_new_mail_bp)
