import os
import upstox_client
from upstox_client.rest import ApiException
from dotenv import load_dotenv

load_dotenv()

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

if not ACCESS_TOKEN:
    raise RuntimeError("❌ ACCESS_TOKEN not found in .env file")


# ==================================================
# CORE FUNCTION
# ==================================================
def place_partial_runner_gtt(
    instrument_token: str,
    desired_entry_price: float,
    current_ltp: float,
    total_quantity: int,
    runner_quantity: int = 10,
    max_slippage: float = 50.0,
    product: str = "D",
    transaction_type: str = "BUY",   # BUY or SELL
):
    """
    Places TWO GTT orders:
    1) Partial exit GTT (Entry + Target + SL)
    2) Runner GTT (Entry only)

    ENTRY PRICE LOGIC:
    - If LTP <= desired_entry → use desired_entry
    - If LTP > desired_entry AND diff <= max_slippage → use LTP
    - Else → abort

    RETURNS:
    {
        entry, target, stoploss,
        profit_qty, runner_qty,
        profit_gtt_ids, runner_gtt_ids
    }
    """

    # =========================
    # VALIDATIONS
    # =========================
    if total_quantity <= runner_quantity:
        raise ValueError("Total quantity must be greater than runner quantity")

    if desired_entry_price <= 0 or current_ltp <= 0:
        raise ValueError("Invalid price values")

    if transaction_type not in ("BUY", "SELL"):
        raise ValueError("transaction_type must be BUY or SELL")

    # =========================
    # ENTRY PRICE LOGIC
    # =========================
    diff = current_ltp - desired_entry_price

    if current_ltp <= desired_entry_price:
        entry_price = desired_entry_price
    elif diff <= max_slippage:
        entry_price = current_ltp
    else:
        raise ValueError(
            f"Entry skipped: LTP too far from desired entry "
            f"(LTP={current_ltp}, Entry={desired_entry_price})"
        )

    exit_quantity = total_quantity - runner_quantity

    # =========================
    # TARGET / SL LOGIC
    # =========================
    if transaction_type == "BUY":
        stoploss_price = round(entry_price - 50, 2)
        target_price   = round(entry_price + 50, 2)
        entry_trigger  = "ABOVE"
    else:
        stoploss_price = round(entry_price + 50, 2)
        target_price   = round(entry_price - 50, 2)
        entry_trigger  = "BELOW"

    # =========================
    # API SETUP
    # =========================
    configuration = upstox_client.Configuration(sandbox=False)
    configuration.access_token = ACCESS_TOKEN

    api = upstox_client.OrderApiV3(
        upstox_client.ApiClient(configuration)
    )

    # =========================
    # GTT RULES
    # =========================
    entry_rule = upstox_client.GttRule(
        strategy="ENTRY",
        trigger_type=entry_trigger,
        trigger_price=float(entry_price)
    )

    target_rule = upstox_client.GttRule(
        strategy="TARGET",
        trigger_type="IMMEDIATE",
        trigger_price=float(target_price)
    )

    stoploss_rule = upstox_client.GttRule(
        strategy="STOPLOSS",
        trigger_type="IMMEDIATE",
        trigger_price=float(stoploss_price)
    )

    # =========================
    # PROFIT LEG (ENTRY + SL + TARGET)
    # =========================
    profit_gtt = upstox_client.GttPlaceOrderRequest(
        type="MULTIPLE",
        quantity=exit_quantity,
        product=product,
        rules=[entry_rule, target_rule, stoploss_rule],
        instrument_token=instrument_token,
        transaction_type=transaction_type
    )

    # =========================
    # RUNNER LEG (ENTRY ONLY)
    # =========================
    runner_gtt = upstox_client.GttPlaceOrderRequest(
        type="SINGLE",
        quantity=runner_quantity,
        product=product,
        rules=[entry_rule],
        instrument_token=instrument_token,
        transaction_type=transaction_type
    )

    # =========================
    # PLACE ORDERS
    # =========================
    try:
        res_profit = api.place_gtt_order(body=profit_gtt)
        res_runner = api.place_gtt_order(body=runner_gtt)

        return {
            "instrument_token": instrument_token,
            "transaction_type": transaction_type,
            "entry": entry_price,
            "target": target_price,
            "stoploss": stoploss_price,
            "profit_qty": exit_quantity,
            "runner_qty": runner_quantity,
            "profit_gtt_ids": res_profit.data.gtt_order_ids,
            "runner_gtt_ids": res_runner.data.gtt_order_ids,
        }

    except ApiException as e:
        raise RuntimeError(f"GTT placement failed: {e}")
