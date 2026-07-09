class TestTimeoutError(TimeoutError):
    __test__ = False
    """Raised when a bus expect() call times out."""


class TestConfigError(RuntimeError):
    __test__ = False
    """Raised when the environment config is invalid."""


class TestGatewayError(RuntimeError):
    __test__ = False
    """Raised when the gateway fails to start or become unavailable."""


class TestDutError(RuntimeError):
    __test__ = False
    """Raised when the DUT fails to respond or configure."""
