"""Pydantic schemas for API requests and responses."""
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from uuid import UUID


class BatchScanRequest(BaseModel):
    force: bool = Field(default=False, description="Re-process even if completed")


class BatchScanResponse(BaseModel):
    batches_found: int
    batches_queued: List[str]
    batches_skipped: List[str]


class BatchItemStatus(BaseModel):
    file_name: str
    file_type: str
    processing_status: Literal["pending", "parsing", "completed", "failed"]
    extracted_products: int = 0
    error_message: Optional[str] = None


class BatchStatus(BaseModel):
    batch_id: str
    status: Literal["discovered", "processing", "completed", "failed", "awaiting_review"]
    supplier_name: Optional[str] = None
    total_files: int = 0
    total_images: int = 0
    total_products: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    items: List[BatchItemStatus] = []


class JobStatus(BaseModel):
    job_id: UUID
    batch_id: str
    job_type: str
    status: Literal["pending", "running", "completed", "failed"]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    result_summary: Optional[dict] = None