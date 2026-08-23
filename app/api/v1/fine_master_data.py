"""API endpoints for managing retailers, SKUs, locations, and carriers."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_master_data_repository
from app.repositories.fine_master_data import MasterDataRepository
from app.schemas.fine_master_data import (
    CarrierRequest,
    CarrierResponse,
    LocationRequest,
    LocationResponse,
    RetailerRequest,
    RetailerResponse,
    SkuRequest,
    SkuResponse,
)

router = APIRouter(tags=["master-data"])


@router.post("/retailers", response_model=RetailerResponse, status_code=201)
def create_retailer(
    body: RetailerRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> dict:
    master_data.add_retailer(
        body.retailer_id,
        body.retailer_name,
        body.priority_tier,
        body.stacking_mode,
    )
    return body.model_dump()


@router.get("/retailers", response_model=list[RetailerResponse])
def list_retailers(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_retailers()


@router.post("/skus", response_model=SkuResponse, status_code=201)
def create_sku(
    body: SkuRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> dict:
    master_data.add_sku(body.sku_id, body.sku_code, body.description)
    return body.model_dump()


@router.get("/skus", response_model=list[SkuResponse])
def list_skus(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_skus()


@router.post("/locations", response_model=LocationResponse, status_code=201)
def create_location(
    body: LocationRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> dict:
    master_data.add_location(
        body.location_id,
        body.location_name,
        body.location_type,
    )
    return body.model_dump()


@router.get("/locations", response_model=list[LocationResponse])
def list_locations(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_locations()


@router.post("/carriers", response_model=CarrierResponse, status_code=201)
def create_carrier(
    body: CarrierRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> dict:
    master_data.add_carrier(
        body.carrier_id,
        body.carrier_name,
        body.historical_reliability_score,
    )
    return body.model_dump()


@router.get("/carriers", response_model=list[CarrierResponse])
def list_carriers(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_carriers()
