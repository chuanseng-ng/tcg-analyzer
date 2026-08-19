"""The worker's entry point — `celery -A tcg_api.analysis.worker worker`.

Celery's `-A` wants a module with a `Celery` instance bound to an attribute at
import time, and `jobs.py` deliberately does not have one: the API imports that
module only to enqueue, and importing it must not require a broker URL. This
module is the one place where the application is actually built, so the process
that needs Redis is the process that fails without it.

Importing `jobs` is what defines the task. It is a `shared_task`, so it attaches
itself to the application built below rather than the other way round.

The worker is started from the API's own image with this command, exactly as the
`migrate` service is — see `infrastructure/local/docker-compose.yml`. Its
isolation (spec §56) is the container's: no published port, no capabilities, no
new privileges.
"""

from __future__ import annotations

from celery import Celery

from tcg_api.analysis.jobs import get_celery_app
from tcg_api.config import get_settings
from tcg_api.logging import configure_logging

__all__ = ["app"]

# So the worker's own output goes through the same structlog pipeline the API
# uses, and the dead-letter line is machine-readable wherever logs are shipped.
configure_logging(get_settings())

app: Celery = get_celery_app()
