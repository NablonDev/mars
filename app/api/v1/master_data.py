"""API endpoints for managing retailers, SKUs, locations, and carriers."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_master_data_repository
from app.repositories.master_data import MasterDataRepository
from app.schemas.master_data import (
    CarrierRequest,
    LocationRequest,
    RetailerRequest,
    SkuRequest,
)

router = APIRouter(tags=["master-data"])


@router.post("/retailers", response_model=RetailerRequest, status_code=201)
def create_retailer(
    body: RetailerRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> RetailerRequest:
    master_data.add_retailer(
        body.retailer_id,
        body.retailer_name,
        body.priority_tier,
        body.stacking_mode,
    )
    return body


@router.get("/retailers", response_model=list[RetailerRequest])
def list_retailers(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_retailers()


@router.post("/skus", response_model=SkuRequest, status_code=201)
def create_sku(
    body: SkuRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> SkuRequest:
    master_data.add_sku(body.sku_id, body.sku_code, body.description)
    return body


@router.get("/skus", response_model=list[SkuRequest])
def list_skus(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_skus()


@router.post("/locations", response_model=LocationRequest, status_code=201)
def create_location(
    body: LocationRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> LocationRequest:
    master_data.add_location(
        body.location_id,
        body.location_name,
        body.location_type,
    )
    return body


@router.get("/locations", response_model=list[LocationRequest])
def list_locations(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_locations()


@router.post("/carriers", response_model=CarrierRequest, status_code=201)
def create_carrier(
    body: CarrierRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> CarrierRequest:
    master_data.add_carrier(
        body.carrier_id,
        body.carrier_name,
        body.historical_reliability_score,
    )
    return body


@router.get("/carriers", response_model=list[CarrierRequest])
def list_carriers(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> list[dict]:
    return master_data.list_carriers()
