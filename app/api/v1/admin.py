"""Admin API endpoints for seeding demo data and simulating daily runs."""

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_fine_seeding_service
from app.schemas.admin import SeedMasterDataResponse, SimulateDailyRunResponse
from app.services.seeding.service import FineSeedingService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed-master-data", response_model=SeedMasterDataResponse)
def seed_master_data(
    force: bool = Query(default=False),
    seeding_service: FineSeedingService = Depends(get_fine_seeding_service),
) -> dict:
    return seeding_service.seed_master_data(force=force)


@router.post("/simulate-daily-run", response_model=SimulateDailyRunResponse)
def simulate_daily_run(
    seeding_service: FineSeedingService = Depends(get_fine_seeding_service),
) -> dict:
    return {"scenarios": seeding_service.simulate_daily_run()}
