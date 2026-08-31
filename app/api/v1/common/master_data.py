"""API endpoints for `common`-schema master data: retailers, retailer-owned
locations, SKUs, materials/material-masters, plants, and carriers.

Was `app/api/v1/fine_master_data.py`."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_master_data_repository
from app.core.envelope import Envelope, success_envelope
from app.core.exceptions import NotFoundError
from app.repositories.common.master_data import MasterDataRepository
from app.schemas.common.master_data import (
    CarrierRequest,
    CarrierResponse,
    MaterialMasterRequest,
    MaterialMasterResponse,
    MaterialRequest,
    MaterialResponse,
    PlantRequest,
    PlantResponse,
    RetailerLocationRequest,
    RetailerLocationResponse,
    RetailerRequest,
    RetailerResponse,
    SkuRequest,
    SkuResponse,
)

router = APIRouter(tags=["master-data"])


@router.post("/retailers", response_model=Envelope[RetailerResponse], status_code=status.HTTP_201_CREATED)
def create_retailer(
    body: RetailerRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[RetailerResponse]:
    created = master_data.add_retailer(**body.model_dump())
    return success_envelope(RetailerResponse.model_validate(created), message="Retailer created.")


@router.get("/retailers", response_model=Envelope[list[RetailerResponse]])
def list_retailers(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[list[RetailerResponse]]:
    rows = [RetailerResponse.model_validate(r) for r in master_data.list_retailers()]
    return success_envelope(rows)


@router.post(
    "/retailers/{retailer_id}/locations",
    response_model=Envelope[RetailerLocationResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_retailer_location(
    retailer_id: UUID,
    body: RetailerLocationRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[RetailerLocationResponse]:
    created = master_data.add_retailer_location(retailer_id=retailer_id, **body.model_dump())
    return success_envelope(RetailerLocationResponse.model_validate(created), message="Location created.")


@router.get("/retailers/{retailer_id}/locations", response_model=Envelope[list[RetailerLocationResponse]])
def list_retailer_locations(
    retailer_id: UUID,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[list[RetailerLocationResponse]]:
    rows = [
        RetailerLocationResponse.model_validate(r) for r in master_data.list_retailer_locations(retailer_id)
    ]
    return success_envelope(rows)


@router.post("/skus", response_model=Envelope[SkuResponse], status_code=status.HTTP_201_CREATED)
def create_sku(
    body: SkuRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[SkuResponse]:
    created = master_data.add_sku(**body.model_dump())
    return success_envelope(SkuResponse.model_validate(created), message="SKU created.")


@router.get("/skus", response_model=Envelope[list[SkuResponse]])
def list_skus(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[list[SkuResponse]]:
    rows = [SkuResponse.model_validate(r) for r in master_data.list_skus()]
    return success_envelope(rows)


@router.post("/materials", response_model=Envelope[MaterialResponse], status_code=status.HTTP_201_CREATED)
def create_material(
    body: MaterialRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[MaterialResponse]:
    created = master_data.add_material(**body.model_dump())
    return success_envelope(MaterialResponse.model_validate(created), message="Material created.")


@router.get("/materials", response_model=Envelope[list[MaterialResponse]])
def list_materials(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[list[MaterialResponse]]:
    rows = [MaterialResponse.model_validate(r) for r in master_data.list_materials()]
    return success_envelope(rows)


@router.post(
    "/material-masters",
    response_model=Envelope[MaterialMasterResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_material_master(
    body: MaterialMasterRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[MaterialMasterResponse]:
    created = master_data.add_material_master(**body.model_dump())
    return success_envelope(
        MaterialMasterResponse.model_validate(created), message="Material master created."
    )


@router.get("/material-masters", response_model=Envelope[list[MaterialMasterResponse]])
def list_material_masters(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[list[MaterialMasterResponse]]:
    rows = [MaterialMasterResponse.model_validate(r) for r in master_data.list_material_masters()]
    return success_envelope(rows)


@router.post("/plants", response_model=Envelope[PlantResponse], status_code=status.HTTP_201_CREATED)
def create_plant(
    body: PlantRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[PlantResponse]:
    created = master_data.add_plant(**body.model_dump())
    return success_envelope(PlantResponse.model_validate(created), message="Plant created.")


@router.get("/plants", response_model=Envelope[list[PlantResponse]])
def list_plants(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[list[PlantResponse]]:
    rows = [PlantResponse.model_validate(r) for r in master_data.list_plants()]
    return success_envelope(rows)


@router.post("/carriers", response_model=Envelope[CarrierResponse], status_code=status.HTTP_201_CREATED)
def create_carrier(
    body: CarrierRequest,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[CarrierResponse]:
    created = master_data.add_carrier(**body.model_dump())
    return success_envelope(CarrierResponse.model_validate(created), message="Carrier created.")


@router.get("/carriers", response_model=Envelope[list[CarrierResponse]])
def list_carriers(
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[list[CarrierResponse]]:
    rows = [CarrierResponse.model_validate(r) for r in master_data.list_carriers()]
    return success_envelope(rows)


@router.get("/carriers/{carrier_id}", response_model=Envelope[CarrierResponse])
def get_carrier(
    carrier_id: UUID,
    master_data: MasterDataRepository = Depends(get_master_data_repository),
) -> Envelope[CarrierResponse]:
    row = master_data.get_carrier(carrier_id)
    if row is None:
        raise NotFoundError(
            code="CARRIER_NOT_FOUND", message=f"No carrier found with carrier_id={carrier_id!r}"
        )
    return success_envelope(CarrierResponse.model_validate(row))
