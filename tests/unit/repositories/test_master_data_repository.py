"""Tests for MasterDataRepository's material_master lookup. Was
tests/unit/repositories/test_po_validation_repositories.py's
`PostgresMaterialMasterRepository` coverage (`MaterialMasterORM`) --
relocated onto `common.material_master`, now looked up by
`(sap_material_number, plant_id)` since `plant` is a real FK'd master-data
row rather than a bare string column (see app/models/common/material.py).
"""

from datetime import date


def test_find_material_master_maps_row_to_dict(repos):
    material = repos.master_data.add_material("MAT-1", None)
    follow_up = repos.master_data.add_material("MAT-SUB", None)
    plant = repos.master_data.add_plant("1000", None, None)
    repos.master_data.add_material_master(
        material_id=material["id"],
        sap_material_number="MAT-1",
        plant_id=plant["id"],
        available_quantity=40,
        follow_up_material_id=follow_up["id"],
        effective_out_date=date(2026, 12, 31),
    )

    record = repos.master_data.find_material_master("MAT-1", plant["id"])

    assert record is not None
    assert record["available_quantity"] == 40
    assert record["follow_up_material_id"] == follow_up["id"]
    assert record["effective_out_date"] == date(2026, 12, 31)


def test_find_material_master_returns_none_when_missing(repos):
    plant = repos.master_data.add_plant("1000", None, None)
    assert repos.master_data.find_material_master("MAT-UNKNOWN", plant["id"]) is None
