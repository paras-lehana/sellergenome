"""Provider exceptions contain only safe operator-facing messages."""


class ProviderUnavailable(Exception):
    def __init__(
        self, message: str, provider: str = "llm", code: int | None = None
    ):
        super().__init__(message)
        self.provider = provider
        self.code = code
