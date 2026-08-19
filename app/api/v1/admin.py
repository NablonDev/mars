"""Admin API endpoints for seeding demo data and simulating daily runs."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_seeding_service
from app.schemas.admin import SeedMasterDataResponse, SimulateDailyRunResponse
from app.services.seeding import SeedingService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed-master-data", response_model=SeedMasterDataResponse)
def seed_master_data(
    seeding_service: SeedingService = Depends(get_seeding_service),
) -> dict:
    return seeding_service.seed_master_data()


@router.post("/simulate-daily-run", response_model=SimulateDailyRunResponse)
def simulate_daily_run(
    seeding_service: SeedingService = Depends(get_seeding_service),
) -> dict:
    return {"scenarios": seeding_service.simulate_daily_run()}
