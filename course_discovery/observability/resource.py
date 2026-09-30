from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

from opentelemetry.sdk.resources import Resource


def build_resource(service_name: str) -> Resource:
    attributes = {"service.name": service_name}
    try:
        attributes["service.version"] = os.getenv("SERVICE_VERSION") or version(
            "course-discovery-agent"
        )
    except PackageNotFoundError:
        pass
    if environment := os.getenv("DEPLOYMENT_ENVIRONMENT"):
        attributes["deployment.environment.name"] = environment
    return Resource.create(attributes)
