from __future__ import annotations

import re


def normalize_identity_key(value: str) -> str:
    """Canonical matching key for a CMIR identity field.

    Strips every non-alphanumeric character and uppercases what's left, so
    "Cust-9900", "cust-9900", "cust9900", and "CUST-9900" all normalize to the
    same value. Used only for lookup/uniqueness comparisons -- the raw field
    value is stored and displayed unchanged.

    migrations/schema.sql's backfill runs the equivalent regex in SQL
    (UPPER(REGEXP_REPLACE(value, '[^A-Za-z0-9]', '', 'g'))); keep the two in
    sync by inspection if this rule ever changes.
    """
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
