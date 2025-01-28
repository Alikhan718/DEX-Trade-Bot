# solana_module/utils.py
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from src.solana_module.solana_client import BondingCurveState, EXPECTED_DISCRIMINATOR, LAMPORTS_PER_SOL, TOKEN_DECIMALS



def get_bonding_curve_address(mint: Pubkey, program_id: Pubkey) -> tuple[Pubkey, int]:
    """
    Вычисляет адрес кривой связывания для данного mint.
    """
    return Pubkey.find_program_address(
        [
            b"bonding-curve",
            bytes(mint)
        ],
        program_id
    )


def find_associated_bonding_curve(mint: Pubkey, bonding_curve: Pubkey) -> Pubkey:
    """
    Находит ассоциированную кривую связывания для данного mint и кривой связывания.
    """
    TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ATA_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

    derived_address, _ = Pubkey.find_program_address(
        [
            bytes(bonding_curve),
            bytes(TOKEN_PROGRAM_ID),
            bytes(mint),
        ],
        ATA_PROGRAM_ID
    )
    return derived_address


async def get_bonding_curve_state(conn: AsyncClient, curve_address: Pubkey) -> BondingCurveState:
    response = await conn.get_account_info(curve_address)
    if not response.value or not response.value.data:
        raise ValueError("Invalid curve state: No data")

    data = response.value.data
    if data[:8] != EXPECTED_DISCRIMINATOR:
        raise ValueError("Invalid curve state discriminator")

    return BondingCurveState(data)


def calculate_bonding_curve_price(curve_state: BondingCurveState) -> float:
    print(curve_state.real_token_reserves)
    print(curve_state.real_sol_reserves)
    if curve_state.virtual_token_reserves <= 0 or curve_state.virtual_sol_reserves <= 0:
        raise ValueError("Invalid reserve state")

    return (curve_state.virtual_sol_reserves / LAMPORTS_PER_SOL) / (
                curve_state.virtual_token_reserves / 10 ** TOKEN_DECIMALS)
