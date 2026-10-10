"""Additive PM access, admin and workshop HTTP interfaces."""

import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBody(BaseModel):
    """Reject ignored fields so callers cannot smuggle unsupported operations."""

    model_config = ConfigDict(extra="forbid")


class LoginBody(StrictBody):
    password: str = Field(min_length=1, max_length=256)


class PasswordBody(StrictBody):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ConfigBody(StrictBody):
    changes: dict
    expected_revision: int = Field(ge=0)


class RollbackBody(StrictBody):
    revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)


class CredentialBody(StrictBody):
    expected_credential_revision: int | None = Field(None, ge=0)
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=8192)
    expected_revision: int = Field(ge=0)


class RunBody(StrictBody):
    scenario_id: str = Field(min_length=1, max_length=100)
    mode: Literal["demo", "live"] = "live"
    seller_id: str | None = Field(None, max_length=120)
    source: str | None = Field(None, max_length=40)
    input: dict | str | None = None

    @field_validator("input")
    @classmethod
    def bounded_input(cls, value):
        if len(json.dumps(value, ensure_ascii=False)) > 18000:
            raise ValueError(
                "Workshop input exceeds the bounded request size."
            )
        if isinstance(value, dict) and set(value) - {
            "text",
            "texts",
            "use_llm",
        }:
            raise ValueError(
                "This workshop action does not accept those input fields."
            )
        if (
            isinstance(value, dict)
            and "use_llm" in value
            and not isinstance(value["use_llm"], bool)
        ):
            raise ValueError("use_llm must be a boolean.")
        return value


class ProgressBody(StrictBody):
    completed: list[str] = Field(max_length=200)


class FeedbackBody(StrictBody):
    target_id: str = Field(min_length=1, max_length=160)
    breadcrumb: str = Field("", max_length=500)
    section_id: str = Field("", max_length=100)
    scenario_id: str | None = Field(None, max_length=100)
    step_id: str | None = Field(None, max_length=100)
    field_id: str | None = Field(None, max_length=100)
    mode: Literal["demo", "live"]
    run_id: str | None = Field(None, max_length=100)
    category: Literal["bug", "idea", "copy", "data", "question"]
    text: str = Field(min_length=1, max_length=8000)
    author_label: str = Field("", max_length=80)
    application_version: str | None = Field(None, max_length=40)
    workshop_version: str | None = Field(None, max_length=40)


class FeedbackPatch(StrictBody):
    status: (
        Literal["new", "triaged", "planned", "in_progress", "done", "declined"]
        | None
    ) = None
    comment: str | None = Field(None, max_length=4000)
    author_label: str = Field("", max_length=80)
    expected_revision: int | None = Field(None, ge=1)


def portal_router(
    config,
    access,
    workshop,
    operator,
    is_operator,
    owner,
    set_owner_cookie,
    workflows,
) -> APIRouter:
    """Bind existing application services without duplicating their business rules."""

    class ServiceReference:
        """Resolve app services at request time, including isolated test lifespans."""

        def __init__(self, resolve):
            self.resolve = resolve

        def __getattr__(self, name):
            return getattr(self.resolve(), name)

    config, access, workshop = (
        ServiceReference(value) for value in (config, access, workshop)
    )
    router = APIRouter(prefix="/api")

    def workshop_owner(request):
        value = owner(request)
        if not value:
            raise HTTPException(
                409,
                "Open the portal or request /api/auth/session to establish browser ownership first.",
            )
        return value

    @router.get("/auth/session")
    async def auth_status(request: Request, response: Response):
        set_owner_cookie(response, request)
        result = access.session(request.cookies.get(access.cookie_name))
        if is_operator(request) and not result["authenticated"]:
            result = {
                "authenticated": True,
                "operator": True,
                "expires_at": None,
                "method": "automation",
            }
        return result

    @router.post("/auth/login")
    async def login(body: LoginBody, request: Request, response: Response):
        identity = request.client.host if request.client else "local"
        token, status = await asyncio.to_thread(
            access.login, body.password, identity
        )
        response.set_cookie(
            access.cookie_name,
            token,
            httponly=True,
            secure=request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https",
            samesite="strict",
            path=config.base_path or "/",
            max_age=int(status["expires_at"] - time.time()),
        )
        set_owner_cookie(response, request)
        return status

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response):
        access.revoke(request.cookies.get(access.cookie_name))
        response.delete_cookie(
            access.cookie_name, path=config.base_path or "/"
        )
        return {"authenticated": False, "operator": False}

    @router.post("/auth/password")
    async def password(
        body: PasswordBody, request: Request, response: Response
    ):
        operator(request)
        if not await asyncio.to_thread(access.verify, body.current_password):
            raise HTTPException(401, "The current password is incorrect.")
        await asyncio.to_thread(access.set_password, body.new_password)
        response.delete_cookie(
            access.cookie_name, path=config.base_path or "/"
        )
        return {
            "changed": True,
            "authenticated": False,
            "sessions_revoked": True,
        }

    @router.get("/admin/config")
    async def admin_config(request: Request):
        operator(request)
        return config.admin.view()

    @router.patch("/admin/config")
    async def save_config(body: ConfigBody, request: Request):
        operator(request)
        return config.admin.update(body.changes, body.expected_revision)

    @router.post("/admin/config/rollback")
    async def rollback_config(body: RollbackBody, request: Request):
        operator(request)
        return config.admin.rollback(body.revision, body.expected_revision)

    @router.get("/admin/config/export")
    async def export_config(request: Request):
        operator(request)
        return config.admin.export()

    @router.put("/admin/credentials")
    async def credentials(body: CredentialBody, request: Request):
        operator(request)
        return config.admin.replace_secret(
            body.name,
            body.value,
            body.expected_revision,
            body.expected_credential_revision,
        )

    @router.get("/sources/targets")
    async def targets(request: Request):
        operator(request)
        return workflows["targets"]()

    @router.get("/workshop/manifest")
    async def manifest():
        return workshop.manifest()

    @router.get("/workshop/runs")
    async def runs(request: Request, limit: int = Query(30, ge=1, le=100)):
        operator(request)
        return {"items": workshop.runs(workshop_owner(request), limit)}

    @router.post("/workshop/runs")
    async def run(body: RunBody, request: Request, response: Response):
        operator(request)
        run_owner = workshop_owner(request)
        scenario = workshop.scenario(body.scenario_id)
        if body.mode == "demo":
            result = {
                "status": "illustrative",
                "execution_origin": "bundled_synthetic",
                "result": scenario["demo"],
                "limitation": scenario["limitation"],
            }
        else:
            try:
                result = await workflows["run"](
                    scenario, body, request, response
                )
            except HTTPException as error:
                result = {
                    "status": "unavailable",
                    "execution_origin": "backend",
                    "result": {"http_status": error.status_code},
                    "limitation": str(error.detail),
                }
        return workshop.save_run(
            {"scenario_id": body.scenario_id, "mode": body.mode, **result},
            run_owner,
        )

    @router.get("/workshop/progress")
    async def progress(request: Request):
        operator(request)
        return workshop.progress(workshop_owner(request))

    @router.patch("/workshop/progress")
    async def save_progress(body: ProgressBody, request: Request):
        operator(request)
        return workshop.progress(workshop_owner(request), body.completed)

    # Static export route precedes identifier routes deliberately.
    @router.get("/feedback/export")
    async def export_feedback(
        request: Request, format: Literal["markdown", "json"] = "json"
    ):
        operator(request)
        content = workshop.export_feedback(format)
        extension = "md" if format == "markdown" else "json"
        return Response(
            content,
            media_type="text/markdown"
            if format == "markdown"
            else "application/json",
            headers={
                "Content-Disposition": f'attachment; filename="pm-feedback.{extension}"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/feedback")
    async def feedback(
        request: Request,
        status: str = Query("", max_length=40),
        category: str = Query("", max_length=40),
        target: str = Query("", max_length=160),
        limit: int = Query(100, ge=1, le=500),
    ):
        operator(request)
        return {"items": workshop.feedback(status, category, target, limit)}

    @router.post("/feedback", status_code=201)
    async def create_feedback(body: FeedbackBody, request: Request):
        operator(request)
        return workshop.create_feedback(body.model_dump(), owner(request))

    @router.patch("/feedback/{identifier}")
    async def update_feedback(
        identifier: str, body: FeedbackPatch, request: Request
    ):
        operator(request)
        return workshop.update_feedback(
            identifier, body.model_dump(exclude_none=True)
        )

    return router
