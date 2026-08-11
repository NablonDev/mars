"""Repository for the dimension tables: retailers, SKUs, locations, carriers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Carrier, Location, Retailer, Sku


class MasterDataRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_retailer(
        self, retailer_id: str, retailer_name: str, priority_tier: str | None, stacking_mode: str = "SUM"
    ) -> None:
        self._session.add(
            Retailer(
                retailer_id=retailer_id,
                retailer_name=retailer_name,
                priority_tier=priority_tier,
                stacking_mode=stacking_mode,
            )
        )
        self._session.flush()

    def list_retailers(self) -> list[dict]:
        rows = self._session.scalars(select(Retailer)).all()
        return [
            {
                "retailer_id": r.retailer_id,
                "retailer_name": r.retailer_name,
                "priority_tier": r.priority_tier,
                "stacking_mode": r.stacking_mode,
            }
            for r in rows
        ]

    def get_stacking_mode(self, retailer_id: str) -> str:
        retailer = self._session.scalars(select(Retailer).where(Retailer.retailer_id == retailer_id)).first()
        return retailer.stacking_mode if retailer else "SUM"

    def add_sku(self, sku_id: str, sku_code: str, description: str | None) -> None:
        self._session.add(Sku(sku_id=sku_id, sku_code=sku_code, description=description))
        self._session.flush()

    def list_skus(self) -> list[dict]:
        rows = self._session.scalars(select(Sku)).all()
        return [{"sku_id": r.sku_id, "sku_code": r.sku_code, "description": r.description} for r in rows]

    def add_location(self, location_id: str, location_name: str | None, location_type: str | None) -> None:
        self._session.add(
            Location(
                location_id=location_id,
                location_name=location_name,
                location_type=location_type,
            )
        )
        self._session.flush()

    def list_locations(self) -> list[dict]:
        rows = self._session.scalars(select(Location)).all()
        return [
            {"location_id": r.location_id, "location_name": r.location_name, "location_type": r.location_type}
            for r in rows
        ]

    def add_carrier(
        self, carrier_id: str, carrier_name: str, historical_reliability_score: float = 90.0
    ) -> None:
        self._session.add(
            Carrier(
                carrier_id=carrier_id,
                carrier_name=carrier_name,
                historical_reliability_score=historical_reliability_score,
            )
        )
        self._session.flush()

    def list_carriers(self) -> list[dict]:
        rows = self._session.scalars(select(Carrier)).all()
        return [
            {
                "carrier_id": r.carrier_id,
                "carrier_name": r.carrier_name,
                "historical_reliability_score": float(r.historical_reliability_score),
            }
            for r in rows
        ]
