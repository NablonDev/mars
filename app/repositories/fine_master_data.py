"""Repository for the dimension tables: retailers, SKUs, locations, carriers."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Carrier, Location, Retailer, Sku


def _retailer_to_dict(r: Retailer) -> dict:
    return {
        "retailer_id": r.retailer_id,
        "retailer_name": r.retailer_name,
        "priority_tier": r.priority_tier,
        "stacking_mode": r.stacking_mode,
        "extension_min_lead_days": r.extension_min_lead_days,
        "extension_response_sla_hours": r.extension_response_sla_hours,
        "extension_fine_threshold": float(r.extension_fine_threshold),
    }


def _sku_to_dict(r: Sku) -> dict:
    return {"sku_id": r.sku_id, "sku_code": r.sku_code, "description": r.description}


def _location_to_dict(r: Location) -> dict:
    return {"location_id": r.location_id, "location_name": r.location_name, "location_type": r.location_type}


def _carrier_to_dict(r: Carrier) -> dict:
    return {
        "carrier_id": r.carrier_id,
        "carrier_name": r.carrier_name,
        "historical_reliability_score": float(r.historical_reliability_score),
    }


class MasterDataRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_retailer(
        self,
        retailer_id: str,
        retailer_name: str,
        priority_tier: str | None,
        stacking_mode: str = "SUM",
        extension_min_lead_days: int = 2,
        extension_response_sla_hours: int = 48,
        extension_fine_threshold: float = 0.0,
    ) -> None:
        self._session.add(
            Retailer(
                retailer_id=retailer_id,
                retailer_name=retailer_name,
                priority_tier=priority_tier,
                stacking_mode=stacking_mode,
                extension_min_lead_days=extension_min_lead_days,
                extension_response_sla_hours=extension_response_sla_hours,
                extension_fine_threshold=extension_fine_threshold,
            )
        )
        self._session.flush()

    def list_retailers(self) -> list[dict]:
        rows = self._session.scalars(select(Retailer)).all()
        return [_retailer_to_dict(r) for r in rows]

    def get_stacking_mode(self, retailer_id: str) -> str:
        retailer = self._session.scalars(select(Retailer).where(Retailer.retailer_id == retailer_id)).first()
        # retailer_id is a DB-level FK on every caller's table, so `retailer`
        # is never actually None here -- the fallback exists only because
        # nothing at the type level proves that to a caller of this method.
        return retailer.stacking_mode if retailer else "SUM"

    def get_extension_policy(self, retailer_id: str) -> dict:
        retailer = self._session.scalars(select(Retailer).where(Retailer.retailer_id == retailer_id)).first()
        # retailer_id is a DB-level FK on every caller's table, so `retailer`
        # is never actually None here -- the fallback exists only because
        # nothing at the type level proves that to a caller of this method.
        if retailer is None:
            return {"min_lead_days": 2, "response_sla_hours": 48, "fine_threshold": 0.0}
        return {
            "min_lead_days": retailer.extension_min_lead_days,
            "response_sla_hours": retailer.extension_response_sla_hours,
            "fine_threshold": float(retailer.extension_fine_threshold),
        }

    def add_sku(self, sku_id: str, sku_code: str, description: str | None) -> None:
        self._session.add(Sku(sku_id=sku_id, sku_code=sku_code, description=description))
        self._session.flush()

    def list_skus(self) -> list[dict]:
        rows = self._session.scalars(select(Sku)).all()
        return [_sku_to_dict(r) for r in rows]

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
        return [_location_to_dict(r) for r in rows]

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
        return [_carrier_to_dict(r) for r in rows]

    def truncate_all(self) -> None:
        """Deletes every retailer/sku/location/carrier row, for a
        force-reseed. Caller must first clear anything that FK-references
        these (fine_rule, sales_order, and sales_order's own dependents) --
        see FineSeedingService._truncate_seeded_tables for the full order."""
        self._session.execute(delete(Carrier))
        self._session.execute(delete(Location))
        self._session.execute(delete(Sku))
        self._session.execute(delete(Retailer))
        self._session.flush()
