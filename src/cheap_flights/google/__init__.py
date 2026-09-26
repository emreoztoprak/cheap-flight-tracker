"""Google Flights access. The only package that knows about Google or fast-flights."""

from .client import GoogleFlights, make_http_client

__all__ = ["GoogleFlights", "make_http_client"]
