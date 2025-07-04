import json
import os
import asyncio
import re
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError, AuthKeyUnregisteredError, AuthRestartError, SessionPasswordNeededError, PasswordHashInvalidError, PhoneCodeInvalidError, PhoneCodeExpiredError
from telethon.network import ConnectionTcpFull
from opentele.api import API
import socks
from rich.console import Console
from text import t
from loguru import logger
from config import load_config
from menu import run_menu
from faker import Faker

logger.remove()
logger.add("autotwofa.log", rotation="10 MB", format="{time:YYYY-MM-DD HH:mm:ss} - {level} - {message}")
logger.add(lambda msg: print(msg, end=""), colorize=True, format="{time:YYYY-MM-DD HH:mm:ss} - {level} - {message}")

console = Console()

sessions_dir = 'sessions'
new_sessions_dir = 'new_sessions'
json_dir = 'sessions'
proxy_file = 'proxy.txt'

running = False

def initialize_files_and_dirs(config):
    for directory in [sessions_dir, new_sessions_dir, json_dir]:
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(t("log.dir_created", locale="en", dir=directory))
            console.print(f"[green]{t('menu.dir_created', locale=config['language'], dir=directory)}[/green]")
    if not os.path.exists(proxy_file):
        with open(proxy_file, 'w') as f:
            f.write("# Формат: host:port:username:password\n")
        logger.info(t("log.proxy_file_created", locale="en"))
        console.print(f"[green]{t('menu.proxy_file_created', locale=config['language'], file=proxy_file)}[/green]")

def load_proxies(config):
    initialize_files_and_dirs(config)
    proxies = []
    with open(proxy_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split(':')
                if len(parts) == 4:
                    host, port, username, password = parts
                    proxies.append((socks.SOCKS5, host, int(port), True, username, password))
                else:
                    logger.warning(t("log.invalid_proxy_format", locale="en", line=line))
    if not proxies:
        logger.error(t("log.proxy_file_empty", locale="en", file=proxy_file))
        console.print(f"[red]{t('menu.proxy_error', locale=config['language'], error=t('error.proxy_empty', locale=config['language']))}[/red]")
    return proxies

def load_auth_data(json_path, config, phone):
    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            auth_data = json.load(f)
        
        required_fields = ['app_id', 'app_hash', 'device', 'sdk', 'app_version', 'lang_pack', 'lang_code', 'system_lang_code']
        missing_fields = [field for field in required_fields if not auth_data.get(field)]
        if missing_fields:
            logger.info(t("log.api_data_missing_generating", locale="en", file=json_path))
            console.print(f"[yellow]{t('menu.api_data_missing_generating', locale=config['language'], file=json_path)}[/yellow]")
            api_data = API.TelegramDesktop.Generate(system="windows", unique_id=phone)
            auth_data['app_id'] = api_data.api_id
            auth_data['app_hash'] = api_data.api_hash
            auth_data['device'] = api_data.device_model
            auth_data['sdk'] = api_data.system_version
            auth_data['app_version'] = api_data.app_version
            auth_data['lang_pack'] = api_data.lang_pack
            auth_data['lang_code'] = api_data.lang_code
            auth_data['system_lang_code'] = api_data.system_lang_code
            with open(json_path, 'w') as f:
                json.dump(auth_data, f, indent=4)
            logger.info(t("log.api_data_generated", locale="en", file=json_path))
            console.print(f"[green]{t('menu.api_data_generated', locale=config['language'], file=json_path)}[/green]")
        return auth_data
    logger.info(t("log.auth_data_missing_creating", locale="en", file=json_path))
    console.print(f"[yellow]{t('menu.auth_data_missing_creating', locale=config['language'], file=json_path)}[/yellow]")
    
    api_data = API.TelegramDesktop.Generate(system="windows", unique_id=phone)
    default_auth_data = {
        "phone": phone,
        "session_file": phone,
        "app_id": api_data.api_id,
        "app_hash": api_data.api_hash,
        "device": api_data.device_model,
        "sdk": api_data.system_version,
        "app_version": api_data.app_version,
        "lang_pack": api_data.lang_pack,
        "lang_code": api_data.lang_code,
        "system_lang_code": api_data.system_lang_code
    }
    with open(json_path, 'w') as f:
        json.dump(default_auth_data, f, indent=4)
    logger.info(t("log.auth_data_created", locale="en", file=json_path))
    console.print(f"[green]{t('menu.auth_data_created', locale=config['language'], file=json_path)}[/green]")
    return default_auth_data

def save_auth_data(json_path, data, config):
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=4)
    logger.info(t("log.auth_data_saved", locale="en", file=json_path))
    console.print(f"[green]{t('menu.auth_data_saved', locale=config['language'], file=json_path)}[/green]")

def extract_code(message):
    pattern = r'(?:Your login code|Ваш код для входа|Login code|Код для входа в Telegram): (\d{5,6})'
    match = re.search(pattern, message)
    return match.group(1) if match else None

async def ensure_connected(client, phone, config):
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logger.error(t("log.connection_failed", locale="en", phone=phone, error=str(e)))
            console.print(f"[red]{t('menu.connection_failed', locale=config['language'], phone=phone, error=str(e))}[/red]")
            return False
    return True

async def generate_random_password():
    fake = Faker('en_US')
    name = fake.first_name()
    year = random.randint(2000, 2025)
    return f"{name}{year}"

async def process_session(json_file, proxies, semaphore, config):
    async with semaphore:
        phone = os.path.splitext(os.path.basename(json_file))[0]
        json_path = os.path.join(json_dir, f"{phone}.json")
        auth_data = load_auth_data(json_path, config, phone)
        if not auth_data:
            return False
        json_filename = os.path.splitext(os.path.basename(json_file))[0]
        phone = auth_data.get('phone')
        if not phone:
            logger.error(t("log.phone_missing", locale="en", file=json_file))
            console.print(f"[red]{t('menu.phone_missing', locale=config['language'], file=json_file)}[/red]")
            return False
        session_file = os.path.join(sessions_dir, auth_data.get('session_file', phone))
        api_id = auth_data.get('app_id')
        api_hash = auth_data.get('app_hash')
        two_fa = auth_data.get('twoFA') or auth_data.get('password')
        lang_pack = auth_data.get('lang_pack')
        lang_code = auth_data.get('lang_code')
        system_lang_code = auth_data.get('system_lang_code')
        device = auth_data.get('device')
        sdk = auth_data.get('sdk')
        app_version = auth_data.get('app_version')
        if not all([api_id, api_hash, lang_pack, lang_code, system_lang_code, device, sdk, app_version]):
            logger.error(t("log.api_data_missing", locale="en", file=json_file))
            console.print(f"[red]{t('menu.api_data_missing', locale=config['language'], file=json_file)}[/red]")
            return False
        if not os.path.exists(session_file + '.session'):
            logger.error(t("log.session_missing", locale="en", phone=phone))
            console.print(f"[red]{t('menu.session_missing', locale=config['language'], phone=phone)}[/red]")
            return False
        logger.info(t("log.using_two_fa", locale="en", phone=phone, two_fa=two_fa or "None"))
        console.print(f"[cyan]{t('menu.using_two_fa', locale=config['language'], phone=phone, two_fa=two_fa or 'None')}[/cyan]")
        proxy = random.choice(proxies) if proxies else None
        if proxy:
            logger.info(t("log.using_proxy_old", locale="en", host=proxy[1], port=proxy[2]))
            console.print(f"[cyan]{t('menu.using_proxy_old', locale=config['language'], host=proxy[1], port=proxy[2])}[/cyan]")
        else:
            logger.info(t("log.no_proxy_old", locale="en", phone=phone))
            console.print(f"[cyan]{t('menu.no_proxy_old', locale=config['language'], phone=phone)}[/cyan]")
        client = TelegramClient(
            session=session_file,
            api_id=api_id,
            api_hash=api_hash,
            connection=ConnectionTcpFull,
            device_model=device,
            system_version=sdk,
            app_version=app_version,
            lang_code=lang_code,
            system_lang_code=system_lang_code,
            proxy=proxy
        )
        client._init_request.lang_pack = lang_pack
        new_session_file = os.path.join(new_sessions_dir, auth_data.get('session_file', phone))
        new_json_file = os.path.join(new_sessions_dir, os.path.basename(json_file))
        
        new_api = API.TelegramDesktop.Generate(system="windows", unique_id=os.path.basename(new_session_file))
        new_proxy = random.choice(proxies) if proxies else None
        if new_proxy:
            logger.info(t("log.using_proxy_new", locale="en", host=new_proxy[1], port=new_proxy[2]))
            console.print(f"[cyan]{t('menu.using_proxy_new', locale=config['language'], host=new_proxy[1], port=new_proxy[2])}[/cyan]")
        else:
            logger.info(t("log.no_proxy_new", locale="en", phone=phone))
            console.print(f"[cyan]{t('menu.no_proxy_new', locale=config['language'], phone=phone)}[/cyan]")
        new_client = TelegramClient(
            session=new_session_file,
            api_id=new_api.api_id,
            api_hash=new_api.api_hash,
            connection=ConnectionTcpFull,
            device_model=new_api.device_model,
            system_version=new_api.system_version,
            app_version=new_api.app_version,
            lang_code=new_api.lang_code,
            system_lang_code=new_api.system_lang_code,
            proxy=new_proxy
        )
        new_client._init_request.lang_pack = new_api.lang_pack
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.error(t("log.session_not_authorized", locale="en", phone=phone))
                console.print(f"[red]{t('menu.session_not_authorized', locale=config['language'], phone=phone)}[/red]")
                return False
            new_two_fa = two_fa
            if config['change_2fa']:
                new_two_fa = await generate_random_password()
                try:
                    if two_fa:
                        
                        await client.edit_2fa(
                            current_password=two_fa,
                            new_password=new_two_fa
                        )
                    else:
                        
                        try:
                            await client.edit_2fa(new_password=new_two_fa)
                        except SessionPasswordNeededError:
                            logger.error(t("log.two_fa_unexpectedly_required", locale="en", phone=phone))
                            console.print(f"[red]{t('menu.two_fa_unexpectedly_required', locale=config['language'], phone=phone)}[/red]")
                            return False
                    logger.info(t("log.two_fa_changed", locale="en", phone=phone, new_two_fa=new_two_fa))
                    console.print(f"[green]{t('menu.two_fa_changed', locale=config['language'], phone=phone, new_two_fa=new_two_fa)}[/green]")
                    logger.info(t("log.waiting_sync", locale="en", seconds=config['retry_delay']))
                    auth_data['twoFA'] = new_two_fa
                    auth_data['password'] = new_two_fa
                    save_auth_data(json_file, auth_data, config)
                    await asyncio.sleep(config['retry_delay'])
                except PasswordHashInvalidError:
                    logger.error(t("log.invalid_two_fa", locale="en", phone=phone, two_fa=two_fa))
                    console.print(f"[red]{t('menu.invalid_two_fa', locale=config['language'], phone=phone, two_fa=two_fa)}[/red]")
                    return False
                except Exception as e:
                    logger.error(t("log.two_fa_change_error", locale="en", phone=phone, error=str(e)))
                    console.print(f"[red]{t('menu.two_fa_change_error', locale=config['language'], phone=phone, error=str(e))}[/red]")
                    return False
            new_auth_data = {
                'phone': phone,
                'session_file': os.path.basename(new_session_file),
                'app_id': new_api.api_id,
                'app_hash': new_api.api_hash,
                'device': new_api.device_model,
                'sdk': new_api.system_version,
                'app_version': new_api.app_version,
                'lang_pack': new_api.lang_pack,
                'lang_code': new_api.lang_code,
                'system_lang_code': new_api.system_lang_code,
                'twoFA': new_two_fa,
                'password': new_two_fa
            }
            if not os.path.exists(new_sessions_dir):
                os.makedirs(new_sessions_dir)
                logger.info(t("log.dir_created", locale="en", dir=new_sessions_dir))
                console.print(f"[green]{t('menu.dir_created', locale=config['language'], dir=new_sessions_dir)}[/green]")
            for attempt in range(3):
                if not await ensure_connected(new_client, phone, config):
                    logger.error(t("log.new_client_connection_failed", locale="en", phone=phone))
                    console.print(f"[red]{t('menu.new_client_connection_failed', locale=config['language'], phone=phone)}[/red]")
                    return False
                try:
                    sent_code = await new_client.send_code_request(phone, force_sms=False)
                    new_auth_data['phone_code_hash'] = sent_code.phone_code_hash
                    for _ in range(3):
                        messages = await client.get_messages(777000, limit=1, wait_time=60)
                        received_code = None
                        if messages and len(messages) > 0:
                            received_code = extract_code(messages[0].message)
                        if received_code:
                            new_auth_data['code'] = received_code
                            logger.info(t("log.code_received", locale="en", phone=phone))
                            console.print(f"[green]{t('menu.code_received', locale=config['language'], phone=phone)}[/green]")
                            break
                        else:
                            logger.warning(t("log.code_not_received", locale="en", phone=phone, seconds=config['retry_delay']))
                            console.print(f"[yellow]{t('menu.code_not_received', locale=config['language'], phone=phone, seconds=config['retry_delay'])}[/yellow]")
                            await asyncio.sleep(config['retry_delay'])
                    if not new_auth_data['code']:
                        logger.error(t("log.code_not_obtained", locale="en", phone=phone))
                        console.print(f"[red]{t('menu.code_not_obtained', locale=config['language'], phone=phone)}[/red]")
                        new_auth_data['code'] = input(t("menu.enter_code", locale=config['language'], phone=phone))
                        if not new_auth_data['code']:
                            logger.error(t("log.code_not_entered", locale="en", phone=phone))
                            console.print(f"[red]{t('menu.code_not_entered', locale=config['language'], phone=phone)}[/red]")
                            return False
                    try:
                        await new_client.sign_in(
                            phone=phone,
                            code=new_auth_data['code'],
                            phone_code_hash=new_auth_data['phone_code_hash']
                        )
                    except SessionPasswordNeededError:
                        try:
                            await new_client.sign_in(password=new_two_fa)
                            logger.info(t("log.new_session_authorized", locale="en", phone=phone))
                            console.print(f"[green]{t('menu.new_session_authorized', locale=config['language'], phone=phone)}[/green]")
                        except PasswordHashInvalidError:
                            logger.error(t("log.invalid_new_two_fa", locale="en", phone=phone, two_fa=new_two_fa))
                            console.print(f"[red]{t('menu.invalid_new_two_fa', locale=config['language'], phone=phone, two_fa=new_two_fa)}[/red]")
                            return False
                        except SessionPasswordNeededError:
                            logger.error(t("log.two_fa_required", locale="en", phone=phone, two_fa=new_two_fa))
                            console.print(f"[red]{t('menu.two_fa_required', locale=config['language'], phone=phone, two_fa=new_two_fa)}[/red]")
                            return False
                    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                        logger.error(t("log.code_invalid_or_expired", locale="en", phone=phone, error=str(e)))
                        console.print(f"[red]{t('menu.code_invalid_or_expired', locale=config['language'], phone=phone, error=str(e))}[/red]")
                        return False
                    except Exception as e:
                        logger.error(t("log.code_usage_error", locale="en", phone=phone, error=str(e)))
                        console.print(f"[red]{t('menu.code_usage_error', locale=config['language'], phone=phone, error=str(e))}[/red]")
                        return False
                    if not await new_client.is_user_authorized():
                        logger.error(t("log.new_session_auth_failed", locale="en", phone=phone))
                        console.print(f"[red]{t('menu.new_session_auth_failed', locale=config['language'], phone=phone)}[/red]")
                        return False
                    for field in ['phone_code_hash', 'code']:
                        new_auth_data.pop(field, None)
                    save_auth_data(new_json_file, new_auth_data, config)
                    if config['logout_old_session']:
                        try:
                            await client.log_out()
                            logger.info(t("log.old_session_closed", locale="en", phone=phone))
                            console.print(f"[green]{t('menu.old_session_closed', locale=config['language'], phone=phone)}[/green]")
                            session_path = session_file + '.session'
                            if os.path.exists(session_path):
                                os.remove(session_path)
                                logger.info(t("log.session_file_deleted", locale="en", file=session_path))
                                console.print(f"[green]{t('menu.session_file_deleted', locale=config['language'], file=session_path)}[/green]")
                            json_path = os.path.join(json_dir, json_filename + '.json')
                            if os.path.exists(json_path):
                                os.remove(json_path)
                                logger.info(t("log.json_file_deleted", locale="en", file=json_path))
                                console.print(f"[green]{t('menu.json_file_deleted', locale=config['language'], file=json_path)}[/green]")
                        except Exception as e:
                            logger.error(t("log.logout_error", locale="en", phone=phone, error=str(e)))
                            console.print(f"[red]{t('menu.logout_error', locale=config['language'], phone=phone, error=str(e))}[/red]")
                            return False
                    return True
                except FloodWaitError as e:
                    logger.error(t("log.flood_limit", locale="en", phone=phone, seconds=e.seconds))
                    console.print(f"[red]{t('menu.flood_limit', locale=config['language'], phone=phone, seconds=e.seconds)}[/red]")
                    return False
                except AuthRestartError:
                    logger.error(t("log.auth_restart_error", locale="en", phone=phone))
                    console.print(f"[red]{t('menu.auth_restart_error', locale=config['language'], phone=phone)}[/red]")
                    if attempt < 2:
                        await asyncio.sleep(config['retry_delay'])
                    else:
                        logger.error(t("log.retries_exhausted", locale="en", phone=phone))
                        console.print(f"[red]{t('menu.retries_exhausted', locale=config['language'], phone=phone)}[/red]")
                        return False
                except Exception as e:
                    if "all available options for this type of number were already used" in str(e):
                        logger.error(t("log.code_options_exhausted", locale="en", phone=phone))
                        console.print(f"[red]{t('menu.code_options_exhausted', locale=config['language'], phone=phone)}[/red]")
                        return False
                    logger.error(t("log.code_request_error", locale="en", phone=phone, error=str(e)))
                    console.print(f"[red]{t('menu.code_request_error', locale=config['language'], phone=phone, error=str(e))}[/red]")
                    if attempt < 2:
                        await asyncio.sleep(config['retry_delay'])
                    else:
                        logger.error(t("log.retries_exhausted", locale="en", phone=phone))
                        console.print(f"[red]{t('menu.retries_exhausted', locale=config['language'], phone=phone)}[/red]")
                        return False
        except AuthKeyUnregisteredError:
            logger.error(t("log.session_unregistered", locale="en", phone=phone))
            console.print(f"[red]{t('menu.session_unregistered', locale=config['language'], phone=phone)}[/red]")
            return False
        except Exception as e:
            logger.error(t("log.session_error", locale="en", phone=phone, error=str(e)))
            console.print(f"[red]{t('menu.session_error', locale=config['language'], phone=phone, error=str(e))}[/red]")
            return False
        finally:
            if client.is_connected():
                await client.disconnect()
            if new_client.is_connected():
                await new_client.disconnect()

async def run_process(config):
    global running
    if running:
        console.print(f"[yellow]{t('menu.running', locale=config['language'])}[/yellow]")
        return
    running = True
    console.print(f"[green]{t('menu.starting_process', locale=config['language'])}[/green]")
    try:
        proxies = load_proxies(config)
        if not proxies:
            running = False
            return
        
        session_files = [f for f in os.listdir(sessions_dir) if f.endswith('.session')]
        json_files = []
        for session_file in session_files:
            phone = os.path.splitext(session_file)[0]
            json_path = os.path.join(json_dir, f"{phone}.json")
            
            json_files.append(os.path.join(json_dir, f"{phone}.json"))
        if not json_files:
            console.print(f"[red]{t('menu.no_json_files', locale=config['language'], dir=json_dir)}[/red]")
            running = False
            return
        semaphore = asyncio.Semaphore(config['max_threads'])
        tasks = [process_session(json_file, proxies, semaphore, config) for json_file in json_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = all(isinstance(result, bool) and result for result in results)
        if success:
            console.print(f"[green]{t('menu.process_completed', locale=config['language'])}[/green]")
        else:
            console.print(f"[red]{t('menu.process_failed', locale=config['language'])}[/red]")
    except Exception as e:
        console.print(f"[red]{t('menu.error', locale=config['language'], error=str(e))}[/red]")
    finally:
        running = False

async def main():
    config = load_config()
    await run_menu(config, run_process)

if __name__ == '__main__':
    asyncio.set_event_loop(asyncio.new_event_loop())
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except Exception as e:
        logger.error(t("log.main_error", locale="en", error=str(e)))
        console.print(f"[red]{t('menu.main_error', locale=load_config()['language'], error=str(e))}[/red]")
    finally:
        loop.close()