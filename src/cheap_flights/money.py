"""Format prices the way people write them."""

_PREFIX = {"USD": "$", "GBP": "£"}
_SUFFIX = {"EUR": " €"}


def money(amount: int, currency: str) -> str:
    """281 €, $99, £50; other currencies keep their code: 4200 TRY."""
    if currency in _PREFIX:
        return f"{_PREFIX[currency]}{amount}"
    return f"{amount}{_SUFFIX.get(currency, ' ' + currency)}"
