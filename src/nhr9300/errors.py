"""Exception hierarchy for the NHR9300 package."""


class NHRError(Exception):
    """Base class for all package errors."""


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
