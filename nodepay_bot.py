import asyncio
import httpx
import time
import uuid
from loguru import logger
from colorama import Fore
from fake_useragent import UserAgent
import os
import signal

def handle_exit(signum, frame):
    """
    Signal handler for graceful script termination on Ctrl+C.
    """
    logger.info("Received Ctrl+C (SIGINT). Exiting gracefully...")
    raise SystemExit(0)

def check_or_create_file(filename, prompt_message):
    """
    Checks if a file exists. If not, prompts the user to create it.
    """
    if not os.path.exists(filename):
        print(f"{filename} not found.")
        print(prompt_message)
        print("Enter the content for the file (each entry on a new line). Press Ctrl+D (or Ctrl+Z + Enter on Windows) when done:")
        try:
            content = []
            while True:
                line = input()
                content.append(line)
        except EOFError:
            pass

        with open(filename, 'w') as file:
            file.write("\n".join(content))
        print(f"{filename} created successfully.")
    else:
        print(f"{filename} found.")

def check_files():
    """
    Ensures `tokens.txt` and `proxies.txt` exist. Prompts the user to create them if missing.
    """
    check_or_create_file(
        'tokens.txt',
        "You need a tokens.txt file containing your NodePay tokens (one per line)."
    )
    check_or_create_file(
        'proxies.txt',
        "You need a proxies.txt file containing your proxy list (one per line)."
    )

def display_header():
    custom_ascii_art = f"""
    {Fore.CYAN}
                      _                        
      _ __   ___   __| | ___ _ __   __ _ _   _ 
     | '_ \\ / _ \\ / _` |/ _ \\ '_ \\ / _` | | | |
     | | | | (_) | (_| |  __/ |_) | (_| | |_| |
     |_| |_|\\___/ \\__,_|\\___| .__/ \\__,_|\\__, |
                            |_|          |___/ 
                                               
           _ __ _   _ _ __  _ __   ___ _ __    
          | '__| | | | '_ \\| '_ \\ / _ \\ '__|   
          | |  | |_| | | | | | | |  __/ |      
          |_|   \\__,_|_| |_|_| |_|\\___|_|      
                                               {Fore.RESET}
    """
    print(custom_ascii_art)
    print(f"{Fore.YELLOW}NODEPAY RUNNER BOT")
    print("Script by Nodebot (Juliwicks)", Fore.RESET)
    print("")

display_header()

# Constants
PING_INTERVAL = 60
RETRIES = 60

DOMAIN_API = {
    "SESSION": "http://api.nodepay.ai/api/auth/session",
    "PING": "https://nw.nodepay.org/api/network/ping"
}

CONNECTION_STATES = {
    "CONNECTED": 1,
    "DISCONNECTED": 2,
    "NONE_CONNECTION": 3
}

status_connect = CONNECTION_STATES["NONE_CONNECTION"]
browser_id = None
account_info = {}
last_ping_time = {}

def uuidv4():
    return str(uuid.uuid4())

def valid_resp(resp):
    if not resp or "code" not in resp or resp["code"] < 0:
        raise ValueError("Invalid response")
    return resp

async def render_profile_info(proxy, token):
    global browser_id, account_info

    try:
        np_session_info = load_session_info(proxy)

        if not np_session_info:
            # Generate new browser_id
            browser_id = uuidv4()
            response = await call_api(DOMAIN_API["SESSION"], {}, proxy, token)
            valid_resp(response)
            account_info = response["data"]
            if account_info.get("uid"):
                save_session_info(proxy, account_info)
                await start_ping(proxy, token)
            else:
                handle_logout(proxy)
        else:
            account_info = np_session_info
            await start_ping(proxy, token)
    except Exception as e:
        logger.error(f"Error in render_profile_info for proxy {proxy}: {e}")
        error_message = str(e)
        if "500 Internal Server Error" in error_message:
            logger.info(f"Removing error proxy from the list: {proxy}")
            remove_proxy_from_list(proxy)
            return None
        else:
            return proxy

async def call_api(url, data, proxy, token):
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": random_user_agent,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(proxies={"http://": proxy, "https://": proxy}, timeout=30) as client:
            response = await client.post(url, json=data, headers=headers)
            response.raise_for_status()
            return valid_resp(response.json())
    except Exception as e:
        logger.error(f"Error during API call: {e}")
        raise ValueError(f"Failed API call to {url}")

async def start_ping(proxy, token):
    try:
        while True:
            await ping(proxy, token)
            await asyncio.sleep(PING_INTERVAL)
    except asyncio.CancelledError:
        logger.info(f"Ping task for proxy {proxy} was cancelled")
    except Exception as e:
        logger.error(f"Error in start_ping for proxy {proxy}: {e}")

async def ping(proxy, token):
    global last_ping_time, RETRIES, status_connect

    current_time = time.time()

    if proxy in last_ping_time and (current_time - last_ping_time[proxy]) < PING_INTERVAL:
        logger.info(f"Skipping ping for proxy { proxy}, not enough time elapsed")
        return

    last_ping_time[proxy] = current_time

    try:
        data = {
            "id": account_info.get("uid"),
            "browser_id": browser_id,
            "timestamp": int(time.time()),
            "version": "2.2.7"
        }

        response = await call_api(DOMAIN_API["PING"], data, proxy, token)
        if response["code"] == 0:
            logger.info(f"Ping successful : {response} with id : {account_info.get('uid')}")
            RETRIES = 0
            status_connect = CONNECTION_STATES["CONNECTED"]
        else:
            handle_ping_fail(proxy, response)
    except Exception as e:
        logger.error(f"Ping failed : {e}")
        handle_ping_fail(proxy, None)

def handle_ping_fail(proxy, response):
    global RETRIES, status_connect

    RETRIES += 1
    if response and response.get("code") == 403:
        handle_logout(proxy)
    elif RETRIES < 2:
        status_connect = CONNECTION_STATES["DISCONNECTED"]
    else:
        status_connect = CONNECTION_STATES["DISCONNECTED"]

def handle_logout(proxy):
    global status_connect, account_info

    status_connect = CONNECTION_STATES["NONE_CONNECTION"]
    account_info = {}
    save_status(proxy, None)
    logger.info(f"Logged out and cleared session info for proxy {proxy}")

def load_proxies(proxy_file):
    try:
        with open(proxy_file, 'r') as file:
            proxies = file.read().splitlines()
        return proxies
    except Exception as e:
        logger.error(f"Failed to load proxies: {e}")
        raise SystemExit("Exiting due to failure in loading proxies")

def load_tokens(token_file):
    try:
        with open(token_file, 'r') as file:
            tokens = file.read().splitlines()
        return tokens
    except Exception as e:
        logger.error(f"Failed to load tokens: {e}")
        raise SystemExit("Exiting due to failure in loading tokens")

def save_status(proxy, status):
    pass

def save_session_info(proxy, data):
    pass

def load_session_info(proxy):
    return {}

def is_valid_proxy(proxy):
    return True

def remove_proxy_from_list(proxy):
    pass

async def main():
    all_proxies = load_proxies('proxies.txt')  # Load proxies from file
    all_tokens = load_tokens('tokens.txt')  # Load tokens from token list

    if not all_tokens:
        print("No tokens found in tokens.txt. Exiting the program.")
        exit()

    while True:
        for token in all_tokens:
            active_proxies = [proxy for proxy in all_proxies if is_valid_proxy(proxy)][:100]
            tasks = {asyncio.create_task(render_profile_info(proxy, token)): proxy for proxy in active_proxies}

            done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                failed_proxy = tasks[task]
                if task.result() is None:
                    logger.info(f"Removing and replacing failed proxy: {failed_proxy}")
                    active_proxies.remove(failed_proxy)
                    if all_proxies:
                        new_proxy = all_proxies.pop(0)
                        if is_valid_proxy(new_proxy):
                            active_proxies.append(new_proxy)
                            new_task = asyncio.create_task(render_profile_info(new_proxy, token))
                            tasks[new_task] = new_proxy
            tasks.pop(task)

            for proxy in set(active_proxies) - set(tasks.values()):
                new_task = asyncio.create_task(render_profile_info(proxy, token))
                tasks[new_task] = proxy
            await asyncio.sleep(3)
        await asyncio.sleep(10)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, handle_exit)
    check_files()
    print("\nRunning now ...")
    try:
        asyncio.run(main())
    except SystemExit:
        logger.info("Program terminated by Ctrl+C.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
