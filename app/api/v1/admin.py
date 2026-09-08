"""Admin API endpoints for seeding demo data and simulating daily runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_penalty_seeding_service
from app.core.envelope import Envelope, success_envelope
from app.schemas.penalties.admin import ScenarioSummary, SeedDataResponse, SimulateDailyRunResponse
from app.services.seeding.service import PenaltySeedingService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed-master-data", response_model=Envelope[SeedDataResponse])
def seed_master_data(
    force: bool = Query(default=False),
    seeding_service: PenaltySeedingService = Depends(get_penalty_seeding_service),
) -> Envelope[SeedDataResponse]:
    """Populate the database with demo master data (retailers, carriers, materials, plants, SKUs)."""
    counts = seeding_service.seed_master_data(force=force)
    return success_envelope(SeedDataResponse.model_validate(counts), message="Seed data applied.")


@router.post("/simulate-daily-run", response_model=Envelope[SimulateDailyRunResponse])
def simulate_daily_run(
    seeding_service: PenaltySeedingService = Depends(get_penalty_seeding_service),
) -> Envelope[SimulateDailyRunResponse]:
    """Simulate a daily operational run: create orders, record fulfillment, and compute penalties."""
    scenarios = [ScenarioSummary.model_validate(s) for s in seeding_service.simulate_daily_run()]
    return success_envelope(SimulateDailyRunResponse(scenarios=scenarios), message="Daily run simulated.")
