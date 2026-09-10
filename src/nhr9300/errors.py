"""Exception hierarchy for the NHR9300 package."""

class NHRError(Exception):
    """Base class for all package errors."""


class NHRTransportError(NHRError):
    """The localhost service could not be reached or timed out.

    This exception never proves that an instrument or workflow is in a safe
    state.  Callers must recover state from the service or verify it through
    the approved independent procedure.
    """


class NHRAPIError(NHRError):
    """A well-formed HTTP error returned by the NHR service."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        error_type: str | None = None,
        code: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.code = code


class NHRProtocolError(NHRError):
    """The service response was malformed or incompatible with this client."""


class NHRWorkflowTimeout(NHRError, TimeoutError):
    """Client-side workflow polling expired without stopping the service run."""


class NHRPendingSnapshotError(NHRError):
    """A publisher must resolve an ambiguous external snapshot submission."""


class NHREvidencePersistenceError(NHRError, OSError):
    """Durable local evidence could not be persisted after bounded retries."""


class NHRConnectionError(NHRError):
    """The instrument could not be reached or the session was lost."""


class NHRDriverError(NHRError):
    """The IVI-COM driver rejected an operation."""


class NHRStateError(NHRError):
    """An operation is not valid in the current state."""


class NHRValidationError(NHRError, ValueError):
    """A command or configuration failed local validation."""


class NHRPolicyError(NHRError):
    """A service policy rejected an otherwise valid operation."""


class NHRNotArmedError(NHRStateError):
    """An energizing operation was requested without a valid arm lease."""


class NHRInterlockError(NHRStateError):
    """One or more safety interlocks are unsafe or stale."""


class NHRRoutineError(NHRError):
    """A test routine failed."""
