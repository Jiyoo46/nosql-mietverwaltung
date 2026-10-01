from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str = "mongodb://localhost:27017/?directConnection=true"
    mongo_db: str = "mietverwaltung"
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
