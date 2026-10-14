from .apis import CareerjetConnector, GetOnBoardConnector, GreenhouseConnector, JoobleConnector, LeverConnector, RemotiveConnector
from .base import Connector, ConnectorError, NotConfigured, RawJob, SearchQuery
from .email_alerts import (
    ComputrabajoAlertsConnector,
    IndeedAlertsConnector,
    LinkedInAlertsConnector,
    ZonaJobsAlertsConnector,
)

# Orden: fuentes prioritarias primero.
CONNECTORS: dict[str, Connector] = {c.key: c for c in [
    LinkedInAlertsConnector(), ZonaJobsAlertsConnector(), ComputrabajoAlertsConnector(), IndeedAlertsConnector(),
    JoobleConnector(), CareerjetConnector(), GetOnBoardConnector(), GreenhouseConnector(), LeverConnector(),
    RemotiveConnector(),
]}

__all__ = ["CONNECTORS", "Connector", "ConnectorError", "NotConfigured", "RawJob", "SearchQuery"]
