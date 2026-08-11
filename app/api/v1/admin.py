"""
Demo/ops endpoints -- seeding master + scenario data via the API (rather
than a script writing straight to the DB) and a one-call reset/replay of
all four worked examples through the real service layer. No auth on
these today: internal/demo system, flagged as an open item in
docs/FINE_ENGINE.md rather than silently ignored -- do not expose this
router outside a trusted network as-is.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_seeding_service
from app.schemas.admin import SeedMasterDataResponse, SimulateDailyRunResponse
from app.services.seeding_service import SeedingService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed-master-data", response_model=SeedMasterDataResponse)
def seed_master_data(seeding_service: SeedingService = Depends(get_seeding_service)) -> dict:
    return seeding_service.seed_master_data()


@router.post("/simulate-daily-run", response_model=SimulateDailyRunResponse)
def simulate_daily_run(seeding_service: SeedingService = Depends(get_seeding_service)) -> dict:
    return {"scenarios": seeding_service.simulate_daily_run()}
