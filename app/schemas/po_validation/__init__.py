"""`app.schemas.po_validation` -- was the flat `app/schemas/po_validation.py`,
split per the approved plan §2/§6 into `purchase_order_lines.py`,
`threads.py`, and `processing_errors.py`.

The pre-Phase-7b module's `PoLine`/`MaterialMasterRecord`/`PoLineError`/
`PoLineStatus` value objects are dropped, not ported: nothing outside that
same file ever imported them (confirmed by search) -- dead value objects
from the pre-restructure design, superseded by the dict-shaped repository
returns `PoValidationService` already works with.
"""

from __future__ import annotations
