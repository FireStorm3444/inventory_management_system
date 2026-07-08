import csv
import io
import logging
import os
import shutil
import tempfile
import urllib.parse
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.database import get_db
from src.core.idempotency import get_idempotency_key
from src.core.security.context import get_tenant_id  # Multi-tenant scoping
from src.domains.catalog.services.ingestion import execute_catalog_ingestion
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/catalog", tags=["Catalog"])


def is_htmx_request(request: Request) -> bool:
    """Helper to detect if the request was initiated by the HTMX frontend."""
    return request.headers.get("HX-Request") == "true"


def _spool_to_temp_file(upload_file: UploadFile) -> str:
    """
    Safely writes an uploaded file to a temporary disk location.
    Runs synchronously but will be dispatched to an async threadpool.
    """
    fd, temp_path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)
    finally:
        upload_file.file.close()

    return temp_path


@router.post("/upload")
async def api_upload_catalog(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Depends(get_idempotency_key),
) -> Any:
    """Idempotent endpoint to securely ingest massive catalog CSV files."""

    if not file.filename or not file.filename.lower().endswith(".csv"):
        msg = "Invalid file type. Only CSV files are accepted."
        if is_htmx_request(request):
            return HTMLResponse(
                content="",
                headers={
                    "HX-Trigger": f'{{"show-toast": {{"level": "error", "message": "{msg}"}}}}'
                },
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    temp_path = await run_in_threadpool(_spool_to_temp_file, file)

    try:
        tenant_id = get_tenant_id()

        # Fire the async pipeline
        total, upserted, invalid = await execute_catalog_ingestion(temp_path, tenant_id, db)

        # Lock in the temporary table merge
        await db.commit()

        logger.info("INGESTION_COMPLETE | Total: %d, Upserted: %d", total, upserted)

        # Content Negotiation & Inline Error Log Generation
        if is_htmx_request(request):
            error_html = ""
            if invalid:
                # Generate in-memory CSV for the failed rows
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=invalid[0].keys())
                writer.writeheader()
                writer.writerows(invalid)

                # Encode into a stateless Data URI
                encoded_csv = urllib.parse.quote(output.getvalue())
                data_uri = f"data:text/csv;charset=utf-8,{encoded_csv}"

                error_html = f"""
                <div class="mt-4 p-4 bg-red-50 border border-red-200 rounded text-sm">
                    <p class="text-red-700 font-bold mb-2">{len(invalid)} rows failed validation.</p>
                    <a href="{data_uri}" download="ingest_errors.csv"
                       class="inline-block px-3 py-1 bg-red-600 text-white rounded shadow-sm hover:bg-red-700">
                        Download Error Log
                    </a>
                </div>
                """

            html = f"""
            <div hx-swap-oob="true" id="ingest-status">
                <span class="text-green-700 font-bold tracking-tight">
                    Successfully updated {upserted} out of {total} SKUs.
                </span>
                {error_html}
            </div>
            """
            return HTMLResponse(
                content=html,
                headers={
                    "HX-Trigger": '{"show-toast": {"level": "success", "message": "Catalog processing finished."}}'
                },
            )

        return {
            "status": "success",
            "total_processed": total,
            "total_upserted": upserted,
            "invalid_rows": len(invalid),
            "errors": invalid if invalid else [],
        }

    except Exception as e:
        await db.rollback()
        logger.error("CATALOG_INGEST_FAILED | Error: %s", str(e))
        raise

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
