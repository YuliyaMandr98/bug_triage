"""Shared Pydantic models and enums."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class IntegrationType(str, Enum):
    """Supported integration providers."""
    CONFLUENCE = "confluence"
    JIRA = "jira"
    GEMINI = "gemini"


class WorkflowType(str, Enum):
    """Workflow identifiers."""
    TRIAGE_BUGS = "triage_bugs"


class RunStatus(str, Enum):
    """Workflow run status lifecycle."""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class IntegrationConnectionStatus(str, Enum):
    """Connection health status."""
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNCONFIGURED = "unconfigured"


# === Schemas for API ===


class IntegrationConfig(BaseModel):
    """Integration provider configuration."""
    type: IntegrationType
    space: Optional[str] = None
    email: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    is_configured: bool = False
    status: IntegrationConnectionStatus = IntegrationConnectionStatus.UNCONFIGURED
    last_tested: Optional[datetime] = None
    error_message: Optional[str] = None

    class Config:
        use_enum_values = True


class WorkflowRunCreate(BaseModel):
    """Request to create a workflow run."""
    workflow_key: Optional[WorkflowType] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    force: bool = False


class TriageBugTicketsRunRequest(BaseModel):
    """Request payload for triage_bugs workflow."""

    jql: str = Field(
        default='issuetype in ("BE BUG", "Mobile bug", Bug, "FE bug") AND status = Backlog',
        description="JQL query selecting bug issues to triage",
    )
    max_results: int = Field(default=50, ge=1, le=500, description="Maximum bugs to process")
    apply: bool = Field(default=False, description="When False, runs in dry-run mode without modifying Jira")
    add_comment: bool = Field(
        default=False,
        description="When True and apply=True, add triage reasoning comments to Jira issues",
    )
    severity_field_id: str = Field(
        default="customfield_10865", description="Jira custom field ID for Severity"
    )
    impact_field_id: str = Field(
        default="customfield_10004", description="Jira custom field ID for Impact"
    )
    target_status: str = Field(
        default="Triage", description="Jira status name to transition confirmed bugs into"
    )
    batch_delay_seconds: int = Field(
        default=10, ge=0, le=60, description="Seconds to wait between Gemini API calls"
    )


class WorkflowRunResponse(BaseModel):
    """Workflow run response."""
    id: str
    workflow_key: WorkflowType
    status: RunStatus
    parameters: dict[str, Any]
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    log_lines: int = 0
    error_message: Optional[str] = None

    class Config:
        use_enum_values = True


class ArtifactResponse(BaseModel):
    """Artifact metadata."""
    id: str
    run_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    download_url: str


class HealthCheckResponse(BaseModel):
    """System health status."""
    status: str  # "ok" or "degraded"
    database: str  # "ok" or "error"
    integrations: dict[str, str]  # integration_name -> status


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    detail: Optional[str] = None
    correlation_id: Optional[str] = None
