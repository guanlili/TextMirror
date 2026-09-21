class FactCheckError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class _FetchError(FactCheckError):
    pass


class _SearchFailure(FactCheckError):
    pass
