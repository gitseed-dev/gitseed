from pydantic import BaseModel


class RootDiscoveryResponse(BaseModel):
    paths: list[str]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    database: str = "connected"
    redis: str = "connected"
    timestamp: str


class VersionResponse(BaseModel):
    version: str
    platform: str

