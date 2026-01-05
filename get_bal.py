import os
import upstox_client
from upstox_client.rest import ApiException
from dotenv import load_dotenv

load_dotenv()
def fetch_balance(token):
    """Attempts to fetch balance. Returns None if token is invalid."""
    configuration = upstox_client.Configuration()
    configuration.access_token = token
    configuration.sandbox = False 

    api_instance = upstox_client.UserApi(upstox_client.ApiClient(configuration))
    
    try:
        api_response = api_instance.get_user_fund_margin('2.0')
        api_response_dict = api_response.to_dict()
        # Safe Dictionary Access to avoid 'AttributeError'
        balance = api_response_dict['data']['equity']['available_margin']
        return balance if balance else None
    except ApiException as e:
        # Check if the error is due to an invalid/expired token
        if e.status == 401:
            return False
        print(f"API Error: {e}")
        return True # Return true to stop the loop even if it's a different error


token = os.getenv("ACCESS_TOKEN")
print(fetch_balance(token))