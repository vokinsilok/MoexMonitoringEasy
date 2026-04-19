from src.connectors.tbank_invest_connector import TBankInvestConnector
from src.core.config import settings
from src.db.database import async_session_maker
from src.moduls.tbank.service import TBankSharesService
from src.tasks.broker import broker
from src.utils.db_manager import DBManager


@broker.task(task_name="tbank.sync_russian_shares")
async def sync_russian_shares_task(
    instrument_status: str = "INSTRUMENT_STATUS_BASE",
    instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
    include_dealer: bool = True,
) -> dict:
    connector = TBankInvestConnector(
        token=settings.TBANK_INVEST_TOKEN,
        base_url=settings.TBANK_INVEST_BASE_URL,
        timeout=settings.TBANK_INVEST_TIMEOUT_SECONDS,
        ssl_verify=settings.TBANK_INVEST_SSL_VERIFY,
        ca_bundle_path=settings.TBANK_INVEST_CA_BUNDLE_PATH,
    )

    async with DBManager(session_factory=async_session_maker) as db:
        async with db.transaction():
            service = TBankSharesService(connector=connector, db=db)
            result = await service.sync_shares_to_db(
                instrument_status=instrument_status,
                instrument_exchange=instrument_exchange,
                russian_only=True,
                include_dealer=include_dealer,
            )
            return result.model_dump()
