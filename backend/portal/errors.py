class PortalError(Exception):
    """Base class for errors the portal raises on purpose."""


class NotFound(PortalError):
    pass


class InvalidRequest(PortalError):
    pass


class OperationInProgress(PortalError):
    pass


class LockMismatch(PortalError):
    pass


class OperationSuperseded(PortalError):
    """The operation was taken away from this worker (declared lost) while it was running."""
