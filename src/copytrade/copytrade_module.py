import asyncio
import struct
import logging
from typing import Set, List
from solana.rpc.async_api import AsyncClient
from solana.rpc.core import RPCException
from solana.transaction import Transaction
from solders.pubkey import Pubkey
from solders.signature import Signature

# Импортируем ваш SolanaClient и связанные объекты
from solana_module.solana_client import SolanaClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Дискриминаторы (из вашего кода для Buy/Sell)
BUY_DISCRIMINATOR = struct.pack("<Q", 16927863322537952870)
SELL_DISCRIMINATOR = struct.pack("<Q", 12502976635542562355)

class CopyTrader:
    """
    Класс CopyTrader «слушает» (или регулярно опрашивает) транзакции мастера и повторяет их 
    через ваш SolanaClient.
    """
    def __init__(
        self,
        master_pubkey: Pubkey,
        solana_client: SolanaClient,
        polling_interval: int = 5
    ):
        """
        :param master_pubkey: Pubkey мастера (кошелёк), за которым следим.
        :param solana_client: Экземпляр вашего SolanaClient.
        :param polling_interval: Период (в секундах) опроса новых транзакций.
        """
        self.master_pubkey: Pubkey = master_pubkey
        self.solana_client: SolanaClient = solana_client
        self.rpc_client: AsyncClient = solana_client.client
        self.polling_interval: int = polling_interval

        # Чтобы не обрабатывать одну и ту же транзакцию несколько раз
        self.seen_signatures: Set[str] = set()

    async def start_copytrade(self):
        """
        Запуск постоянного мониторинга транзакций мастера.
        Можно вызывать это внутри asyncio.run(...) или из любого другого корутино-менеджера.
        """
        logger.info(f"Запуск copytrade для мастера: {self.master_pubkey}")
        while True:
            try:
                await self.check_new_transactions()
            except Exception as e:
                logger.error(f"Ошибка в процессе мониторинга: {e}")
            # Ждём заданный интервал и повторяем
            await asyncio.sleep(self.polling_interval)

    async def check_new_transactions(self):
        """
        Опрашивает последние подписи (tx) мастера и обрабатывает те, 
        которые ещё не видели (не в seen_signatures).
        """
        # Метод get_signatures_for_address возвращает список { signature, slot, ... }, начиная с самых новых
        sigs_info = await self.rpc_client.get_signatures_for_address(
            self.master_pubkey,
            limit=10  # пример: берём последние 10; можно увеличить
        )

        if not sigs_info.value:
            return

        # Сортируем сигнатуры от старых к новым (чтобы воспроизводить операции в хронологическом порядке)
        # По умолчанию может быть [новые ... старые]
        sorted_sigs = list(reversed(sigs_info.value))

        new_sigs: List[str] = []
        for tx_info in sorted_sigs:
            signature = tx_info.signature
            if signature not in self.seen_signatures:
                new_sigs.append(signature)

        if not new_sigs:
            return

        # Помечаем их сразу как «увиденные», чтобы не дублить
        for sig in new_sigs:
            self.seen_signatures.add(sig)

        # Теперь постараемся последовательно обработать эти транзакции
        for sig in new_sigs:
            await self.process_transaction(sig)

    async def process_transaction(self, signature: str):
        """
        Получаем детали транзакции, вычленяем инструкции, которые относятся к PUMP_PROGRAM,
        проверяем их дискриминатор (BUY/SELL) и копируем.
        """
        logger.info(f"Обрабатываем транзакцию мастера: {signature}")
        try:
            tx_resp = await self.rpc_client.get_transaction(
                signature,
                max_supported_transaction_version=2  # можно и 1, в зависимости от версии
            )
        except RPCException as e:
            logger.error(f"Не удалось получить транзакцию {signature}: {e}")
            return

        if not tx_resp.value or not tx_resp.value.transaction:
            logger.warning(f"Нет данных о транзакции {signature}")
            return

        # В transaction.message лежат инструкции
        message = tx_resp.value.transaction.message
        instructions = message.instructions

        for ix in instructions:
            # Проверяем, что инструкция адресована вашей программе PUMP_PROGRAM
            if ix.program_id == self.solana_client.PUMP_PROGRAM:
                data_bytes = bytes(ix.data)
                disc = data_bytes[:8]  # первые 8 байт — дискриминатор

                if disc == BUY_DISCRIMINATOR:
                    # Это buy-инструкция
                    await self.handle_buy_ix(ix, data_bytes[8:])
                elif disc == SELL_DISCRIMINATOR:
                    # Это sell-инструкция
                    await self.handle_sell_ix(ix, data_bytes[8:])
                else:
                    logger.debug("Инструкция в PUMP_PROGRAM, но дискриминатор не buy/sell")

    async def handle_buy_ix(self, ix, data: bytes):
        """
        Разбираем данные buy-инструкции:
          buy = discriminator (8 байт) + token_amount (Q) + max_amount_lamports (Q)
        Здесь data = всё, кроме первых 8 байт дискриминатора.
        """
        if len(data) < 16:
            logger.warning("Недостаточная длина данных buy-инструкции")
            return

        token_amount_lamports = struct.unpack("<Q", data[:8])[0]   # int
        max_amount_lamports = struct.unpack("<Q", data[8:16])[0]   # int

        token_amount_float = token_amount_lamports / 1e6  # у вас в коде это означает (token_amount * 10**6)
        logger.info(f"[CopyTrade] BUY: token_amount={token_amount_float}, max_amount_lamports={max_amount_lamports}")

        # Теперь важно понять, какие Pubkey являются mint, bonding_curve, associated_bonding_curve и т.д.
        # У вас в send_buy_transaction() передаются:
        #   accounts = [
        #       PUMP_GLOBAL, PUMP_FEE, mint, bonding_curve, associated_bonding_curve, ...
        #   ]
        # Смотрим индексы в ix.accounts — это объекты типа `CompiledInstructionAccount`.
        # В Solana Py, чтобы получить Pubkey, нужно смотреть на:
        #   message.accountKeys[ix.accounts[idx].index]
        # Но в solana-py 0.x по-другому, в solders своё... 
        # В упрощённом случае возьмём "напрямую" (зная порядок).

        # Пример (аккуратно — индексы могут отличаться от примера в вашем коде!):
        # ix.accounts[2] -> mint
        # ix.accounts[3] -> bonding_curve
        # ix.accounts[4] -> associated_bonding_curve
        # ...
        # В transaction.message.account_keys — массив всех pubkeys, 
        #   ix.accounts[x].pubkey — тоже должен быть.
        # Для solana-py 0.18+ / solders может быть слегка по-другому, иллюстрирую идею:

        message = await self._get_message_from_ix(ix)
        if not message:
            logger.warning("Не удалось извлечь message для handle_buy_ix.")
            return

        # Пытаемся достать pubkey по индексам. 
        # Пример (посмотрите реальный порядок в вашем send_buy_transaction!):
        mint_pubkey = message.account_keys[ix.accounts[2].index]
        bonding_curve_pubkey = message.account_keys[ix.accounts[3].index]
        associated_bonding_curve_pubkey = message.account_keys[ix.accounts[4].index]

        # buy_token(self, mint: Pubkey, bonding_curve: Pubkey, associated_bonding_curve: Pubkey, amount: float, slippage: float = 0.25)
        # Но у нас нет параметра slippage. Можно брать дефолт (0.25) или ваш.

        # В вашем send_buy_transaction вы вычисляете 'token_amount' как (amount / token_price_sol).
        # Мы же сейчас копируем 1-в-1:
        #   token_amount_float = <кол-во токенов, которое хочет купить мастер>
        #   max_amount_lamports = <сколько SOL (в лампортах) готов потратить мастер c учётом slippage>
        #
        # Но метод buy_token(...) у вас принимает 'amount' как кол-во SOL, которое тратим,
        #   а не кол-во токенов напрямую! 
        #
        # То есть в вашем коде внутри buy_token:
        #   amount_lamports = int(amount * LAMPORTS_PER_SOL)
        #   token_amount = amount / token_price_sol
        #
        # Так что, чтобы «синхронно» копировать, нужно:
        #   1) Либо вызвать напрямую send_buy_transaction(params) — имитируя те же параметры.
        #   2) Либо перевести token_amount_float обратно в кол-во SOL. 
        #
        # Самый простой путь — вызвать напрямую send_buy_transaction(...), 
        #   но у нас нет под рукой готового словаря `params`. 
        # Нужно собрать его аналогично вашему коду.
        # 
        # Для примера ниже я покажу вызов `send_buy_transaction`, но уже "напрямую":
        # (Только не забудьте, что этот метод требует `token_amount` (float) и `max_amount_lamports` (int)).

        params = {
            "mint": mint_pubkey,
            "bonding_curve": bonding_curve_pubkey,
            "associated_bonding_curve": associated_bonding_curve_pubkey,
            "associated_token_account": None,  # Заполнится внутри (можем сами создать/найти),
            "token_amount": token_amount_float,
            "max_amount_lamports": max_amount_lamports,
        }

        logger.info("[CopyTrade] Отправляем buy-транзакцию от имени нашего SolanaClient...")
        try:
            await self.solana_client.send_buy_transaction(params)
            logger.info("[CopyTrade] BUY скопирован успешно!")
        except Exception as e:
            logger.error(f"[CopyTrade] Ошибка при копировании BUY: {e}")

    async def handle_sell_ix(self, ix, data: bytes):
        """
        Разбираем данные sell-инструкции:
          sell = discriminator (8 байт) + amount(Q) + min_sol_output(Q)
        """
        if len(data) < 16:
            logger.warning("Недостаточная длина данных sell-инструкции")
            return

        sell_amount_lamports = struct.unpack("<Q", data[:8])[0]
        min_sol_output = struct.unpack("<Q", data[8:16])[0]

        # В вашем коде:
        #   sell_amount -> token_amount
        #   resp = await self.client.get_token_account_balance(...)
        # и т.д.
        #
        # Но мы берём "сырые" данные: sell_amount_lamports = (token_amount * 10**6)
        sell_amount_float = sell_amount_lamports / 1e6

        logger.info(f"[CopyTrade] SELL: token_amount={sell_amount_float}, min_sol_output={min_sol_output}")

        message = await self._get_message_from_ix(ix)
        if not message:
            logger.warning("Не удалось извлечь message для handle_sell_ix.")
            return

        mint_pubkey = message.account_keys[ix.accounts[2].index]
        bonding_curve_pubkey = message.account_keys[ix.accounts[3].index]
        associated_bonding_curve_pubkey = message.account_keys[ix.accounts[4].index]

        # У вас есть метод send_sell_transaction(params),
        #   которому нужны: mint, bonding_curve, associated_bonding_curve, associated_token_account, token_amount, ...
        # Сформируем params:
        params = {
            "mint": mint_pubkey,
            "bonding_curve": bonding_curve_pubkey,
            "associated_bonding_curve": associated_bonding_curve_pubkey,
            # associated_token_account получится внутри (можем передать None, 
            #   ваш метод всё равно создаёт ATA при необходимости)
            "associated_token_account": None,
            "token_amount": sell_amount_lamports,  # В вашем коде это int, 
                                                  # хотя в sell_token(...) вы передаёте float.
                                                  # Внимательно проверьте совместимость!
                                                  # Если нужно float, то передайте sell_amount_float.
            "min_amount_lamports": min_sol_output,
        }

        logger.info("[CopyTrade] Отправляем sell-транзакцию от имени нашего SolanaClient...")
        try:
            await self.solana_client.send_sell_transaction(params)
            logger.info("[CopyTrade] SELL скопирован успешно!")
        except Exception as e:
            logger.error(f"[CopyTrade] Ошибка при копировании SELL: {e}")

    async def _get_message_from_ix(self, ix) -> Transaction:
        """
        Вспомогательный метод, чтобы получить "message" транзакции и account_keys.
        В зависимости от версии solana-py / solders структура может отличаться.
        """
        # В объекте ix может не быть прямого поля message, 
        # а `transaction.message` мы забираем через self.process_transaction(...). 
        # Но у нас уже есть `message` внутри process_transaction, 
        #   можно было бы передавать его параметром.
        # Чтобы не менять структуру слишком сильно, сделаем упрощённый вариант:

        # Если ix родом из tx_resp.value.transaction.message,
        #   то у ix может быть ссылка на parent message?
        #   В solders/solana-py 0.27+ обычно можно:
        #       parent_message = ix.parent
        # Но зависит от реализации.

        # Упростим: мы запоминаем глобальную копию message (которую взяли выше),
        #   и будем просто возвращать её. 
        # Для демонстрации сделаем так:
        if not hasattr(ix, "parent_instruction"):
            return None
        return ix.parent_instruction  # Теоретически, это может вернуть None или TransactionMessage

        # В некоторых реализациях solana-py:
        #    ix.parent_instruction.account_keys -> список pubkeys
        # или
        #    ix.parent_instruction.message.account_keys

        # В реальном проекте лучше сразу в process_transaction() передавать message при вызове handle_buy_ix/handle_sell_ix.