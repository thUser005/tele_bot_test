import os
from datetime import datetime
import pytz

from dotenv import load_dotenv
import upstox_client
from upstox_client.rest import ApiException

# =========================
# CONFIG
# =========================
load_dotenv()

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
API_VERSION = "2.0"
IST = pytz.timezone("Asia/Kolkata")

if not ACCESS_TOKEN:
    raise RuntimeError("❌ ACCESS_TOKEN not found in environment")

# =========================
# TOKEN STATUS CHECK
# =========================
def check_upstox_access_token():
    """
    Checks whether the Upstox access token is:
    - VALID
    - EXPIRED / INVALID
    - MAINTENANCE
    """

    config = upstox_client.Configuration()
    config.access_token = ACCESS_TOKEN

    api_client = upstox_client.ApiClient(config)
    api = upstox_client.UserApi(api_client)

    checked_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    try:
        api.get_profile(api_version=API_VERSION)

        return {
            "status": "VALID",
            "checked_at": checked_at
        }

    except ApiException as e:
        err = str(e)

        # 🔒 Maintenance
        if "423" in err or "Locked" in err:
            return {
                "status": "MAINTENANCE",
                "checked_at": checked_at
            }

        # ❌ Token expired / invalid
        if "401" in err or "Unauthorized" in err:
            return {
                "status": "EXPIRED",
                "checked_at": checked_at
            }

        # ❗ Unknown error
        return {
            "status": "ERROR",
            "message": err,
            "checked_at": checked_at
        }

# # =========================
# # CLI RUN
# # =========================
# if __name__ == "__main__":
#     result = check_upstox_access_token()

#     print("====================================")
#     print("🔍 Upstox Access Token Status")
#     print("====================================")

#     for k, v in result.items():
#         print(f"{k.upper():<12}: {v}")

#     print("====================================")
