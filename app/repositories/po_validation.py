from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select, update

from app.db.session import Database
from app.models.po_validation import MaterialMasterORM, PoLineErrorORM, PoLineORM
from app.schemas.po_validation import MaterialMasterRecord, PoLine, PoLineError

logger = logging.getLogger(__name__)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class PostgresPoLineRepository:
    """SQLAlchemy repository for po_lines."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def create(self, line: PoLine) -> str:
        with self._db.session() as session:
            record = PoLineORM(
                batch_id=line.batch_id,
                po_number=line.po_number,
                po_line_number=line.po_line_number,
                customer_id=line.customer_id,
                customer_material_code=line.customer_material_code,
                plant=line.plant,
                order_quantity=line.order_quantity,
                uom=line.uom,
                requested_delivery_date=line.requested_delivery_date,
                raw_payload=line.raw_payload,
                status=line.status,
            )
            session.add(record)
            session.flush()
            po_line_id = record.id
        logger.info("Created po_line %s for batch %s", po_line_id, line.batch_id)
        return po_line_id

    def get(self, po_line_id: Any) -> Optional[Dict[str, Any]]:
        with self._db.session() as session:
            row = session.scalar(select(PoLineORM).where(PoLineORM.id == po_line_id))
        if row is None:
            return None
        return self._row_to_dict(row)

    def update_status(self, po_line_id: Any, status: str) -> None:
        with self._db.session() as session:
            session.execute(
                update(PoLineORM).where(PoLineORM.id == po_line_id).values(status=status)
            )
        logger.info("Updated po_line %s to status %s", po_line_id, status)

    def list_by_status(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> tuple[list[Dict[str, Any]], Optional[str]]:
        stmt = select(PoLineORM)
        if status is not None:
            stmt = stmt.where(PoLineORM.status == status)
        if cursor is not None:
            stmt = stmt.where(PoLineORM.updated_at < cursor)
        stmt = stmt.order_by(PoLineORM.updated_at.desc()).limit(limit)

        with self._db.session() as session:
            rows = session.scalars(stmt).all()

        items = [self._row_to_dict(row) for row in rows]
        next_cursor = items[-1]["updated_at"] if len(items) == limit and items else None
        return items, next_cursor

    @staticmethod
    def _row_to_dict(row: PoLineORM) -> Dict[str, Any]:
        return {
            "id": str(row.id),
            "batch_id": row.batch_id,
            "po_number": row.po_number,
            "po_line_number": row.po_line_number,
            "customer_id": row.customer_id,
            "customer_material_code": row.customer_material_code,
            "plant": row.plant,
            "order_quantity": float(row.order_quantity),
            "uom": row.uom,
            "status": row.status,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }


class PostgresMaterialMasterRepository:
    """SQLAlchemy repository for the local SAP Material Master mirror."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def find(self, sap_material_number: str, plant: str) -> Optional[MaterialMasterRecord]:
        with self._db.session() as session:
            row = session.scalar(
                select(MaterialMasterORM).where(
                    and_(
                        MaterialMasterORM.sap_material_number == sap_material_number,
                        MaterialMasterORM.plant == plant,
                    )
                )
            )
        if row is None:
            return None
        return MaterialMasterRecord(
            sap_material_number=row.sap_material_number,
            plant=row.plant,
            available_quantity=float(row.available_quantity),
            description=row.description,
            uom=row.uom,
            discontinuation_indicator=row.discontinuation_indicator,
            effective_out_date=_iso(row.effective_out_date),
            follow_up_material_number=row.follow_up_material_number,
        )


class PostgresPoLineErrorRepository:
    """SQLAlchemy repository for po_line_errors."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def log(self, error: PoLineError) -> int:
        with self._db.session() as session:
            record = PoLineErrorORM(
                po_line_id=error.po_line_id,
                agent_run_id=error.agent_run_id,
                error_type=error.error_type,
                error_code=error.error_code,
                error_message=error.error_message,
                node_name=error.node_name,
                raw_error_detail=error.raw_error_detail,
            )
            session.add(record)
            session.flush()
            error_id = record.id
        logger.info("Logged po_line_error %s for po_line %s (%s)", error_id, error.po_line_id, error.node_name)
        return error_id

    def list_for_po_line(self, po_line_id: Any) -> List[Dict[str, Any]]:
        with self._db.session() as session:
            rows = session.scalars(
                select(PoLineErrorORM)
                .where(PoLineErrorORM.po_line_id == po_line_id)
                .order_by(PoLineErrorORM.occurred_at.desc())
            ).all()
        return [
            {
                "id": str(row.id),
                "po_line_id": str(row.po_line_id),
                "agent_run_id": row.agent_run_id,
                "error_type": row.error_type,
                "error_code": row.error_code,
                "error_message": row.error_message,
                "node_name": row.node_name,
                "occurred_at": _iso(row.occurred_at),
                "resolved": row.resolved,
            }
            for row in rows
        ]
