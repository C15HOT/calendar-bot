from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from functools import lru_cache


class Settings(BaseSettings):
    # POSTGRESQL_SERVER: str
    # POSTGRESQL_USER: str
    # POSTGRESQL_PASS: str
    # POSTGRESQL_DB: str

    # base_url: str
    bot_token: str
    server_address: str
    is_debug: str
    admin_id: str


    @property
    def scopes(self):
        return [
        'https://www.googleapis.com/auth/calendar.readonly',  # Для чтения событий
        'https://www.googleapis.com/auth/calendar.events'  # Для создания, редактирования и удаления событий
    ]


    @property
    def echo(self):
        if self.is_debug == 'True':
            return True
        else:
            return False
    @property
    def webhook_url(self):
        return f"{self.server_address}/webhook"
    # @property
    # def async_session(self):
    #     connection = f'{self.POSTGRESQL_SERVER}{self.POSTGRESQL_DB}'
    #     engine = create_async_engine(connection, echo=self.echo)
    #     async_session = async_sessionmaker(engine, expire_on_commit=False)
    #     return async_session

    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'

@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    return settings
