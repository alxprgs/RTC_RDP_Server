import contextvars

REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
