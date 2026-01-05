import os
from dotenv import load_dotenv
import upstox_client
from upstox_client.rest import ApiException

load_dotenv()
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

if not ACCESS_TOKEN:
    raise RuntimeError("ACCESS_TOKEN missing in .env")


def fetch_ltp(instrument_token: str) -> float:
    """
    Fetch LTP by matching instrument_token inside Upstox response
    """

    try:
        config = upstox_client.Configuration()
        config.access_token = ACCESS_TOKEN

        api = upstox_client.MarketQuoteApi(
            upstox_client.ApiClient(config)
        )

        resp = api.ltp(instrument_token, "2.0")
        data = resp.to_dict()

        if "data" not in data or not data["data"]:
            raise RuntimeError("Empty LTP response from Upstox")

        # 🔍 Match by instrument_token
        for _, quote in data["data"].items():
            if quote.get("instrument_token") == instrument_token:
                ltp = quote.get("last_price")
                if ltp is None:
                    raise RuntimeError("LTP returned but price is None")
                return float(ltp)

        raise RuntimeError(
            f"LTP not found for instrument_token={instrument_token}\n"
            f"Returned keys={list(data['data'].keys())}"
        )

    except ApiException as e:
        raise RuntimeError(
            f"Upstox LTP API Error\n"
            f"Status: {e.status}\n"
            f"Reason: {e.reason}\n"
            f"Body: {e.body}"
        )


# if __name__ == "__main__":
#     fetch_ltp_debug("BSE_FO|1160087")
