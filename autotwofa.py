import json
import os
import asyncio
import re
import logging
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError, AuthKeyUnregisteredError, AuthRestartError, SessionPasswordNeededError, PasswordHashInvalidError, PhoneCodeInvalidError, PhoneCodeExpiredError
from telethon.network import ConnectionTcpFull
from opentele.api import API
import socks

MAX_THREADS = 5
RETRY_DELAY = 20
LOGOUT_OLD_SESSION = True
CHANGE_2FA = True

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('telethon').setLevel(logging.WARNING)

sessions_dir = 'sessions'
new_sessions_dir = 'new_sessions'
json_dir = 'sessions'
proxy_file = 'proxy.txt'
config_file = 'config.json'

def initialize_files_and_dirs():
    for directory in [sessions_dir, new_sessions_dir, json_dir]:
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"Создана папка: {directory}")
    if not os.path.exists(proxy_file):
        with open(proxy_file, 'w') as f:
            f.write("# Формат: host:port:username:password\n")
            logger.info(f"Создан файл proxy.txt с примером формата")
    if not os.path.exists(config_file):
        default_config = {"max_threads": MAX_THREADS}
        with open(config_file, 'w') as f:
            json.dump(default_config, f, indent=4)
            logger.info(f"Создан файл config.json с настройками по умолчанию")

def load_proxies():
    initialize_files_and_dirs()
    proxies = []
    with open(proxy_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split(':')
                if len(parts) == 4:
                    host, port, username, password = parts
                    proxies.append((socks.SOCKS5, host, int(port), True, username, password))
    return proxies

def load_auth_data(json_path):
    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            return json.load(f)
    return {}

def save_auth_data(json_path, data):
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=4)

def load_config():
    initialize_files_and_dirs()
    with open(config_file, 'r') as f:
        config = json.load(f)
    return config.get('max_threads', MAX_THREADS)

def extract_code(message):
    pattern = r'(?:Your login code|Ваш код для входа|Login code|Код для входа в Telegram): (\d{5,6})'
    match = re.search(pattern, message)
    return match.group(1) if match else None

async def ensure_connected(client, phone):
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logger.error(f"Не удалось переподключиться для {phone}: {e}")
            return False
    return True

async def generate_random_password(length=12):
    import string
    characters = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(characters) for _ in range(length))

async def process_session(json_file, proxies, semaphore, max_retries=3, retry_delay=RETRY_DELAY):
    async with semaphore:
        auth_data = load_auth_data(json_file)
        if not auth_data:
            logger.error(f"Не удалось загрузить данные из {json_file}")
            return False
        json_filename = os.path.splitext(os.path.basename(json_file))[0]
        phone = auth_data.get('phone')
        if not phone:
            logger.error(f"Номер телефона отсутствует в {json_file}")
            return False
        session_file = os.path.join(sessions_dir, auth_data.get('session_file', phone))
        api_id = auth_data.get('app_id')
        api_hash = auth_data.get('app_hash')
        two_fa = auth_data.get('twoFA') or auth_data.get('password')
        lang_pack = auth_data.get('lang_pack', 'tdesktop')
        lang_code = auth_data.get('lang_code', 'en')
        system_lang_code = auth_data.get('system_lang_code', 'en-US')
        device = auth_data.get('device', 'Desktop')
        sdk = auth_data.get('sdk', 'Windows 10')
        app_version = auth_data.get('app_version', '3.4.3 x64')
        if not all([api_id, api_hash]):
            logger.error(f"Недостаточно данных в {json_file}: api_id={api_id}, api_hash={api_hash}")
            return False
        if not isinstance(lang_pack, str):
            lang_pack = "tdesktop"
        if not isinstance(lang_code, str):
            lang_code = "en"
        if not isinstance(system_lang_code, str):
            system_lang_code = "en-US"
        if not os.path.exists(session_file + '.session'):
            logger.error(f"Сессия для {phone} не найдена в {sessions_dir}")
            return False
        logger.info(f"Используемый пароль 2FA для {phone}: {two_fa}")
        proxy = random.choice(proxies) if proxies else None
        if proxy:
            logger.info(f"Используется прокси для старой сессии: {proxy[1]}:{proxy[2]}")
        else:
            logger.info(f"Прокси не используются для старой сессии {phone}")
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
            logger.info(f"Используется прокси для новой сессии: {new_proxy[1]}:{new_proxy[2]}")
        else:
            logger.info(f"Прокси не используются для новой сессии {phone}")
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
                logger.error(f"Сессия для {phone} не авторизована. Необходимо выполнить авторизацию.")
                return False
            new_two_fa = two_fa
            if CHANGE_2FA:
                new_two_fa = await generate_random_password()
                try:
                    await client.edit_2fa(
                        current_password=two_fa if two_fa else None,
                        new_password=new_two_fa
                    )
                    logger.info(f"2FA пароль для {phone} успешно изменен на старой сессии на {new_two_fa}")
                    logger.info(f"Ожидание {retry_delay} секунд для синхронизации нового 2FA")
                    auth_data['twoFA'] = new_two_fa
                    auth_data['password'] = new_two_fa
                    save_auth_data(json_file, auth_data)
                    await asyncio.sleep(retry_delay)
                except PasswordHashInvalidError:
                    logger.error(f"Неверный текущий пароль 2FA для {phone}: {two_fa}")
                    return False
                except Exception as e:
                    logger.error(f"Ошибка при смене 2FA на старой сессии для {phone}: {e}")
                    return False
            new_auth_data = auth_data.copy()
            new_auth_data['lang_pack'] = lang_pack
            new_auth_data['lang_code'] = lang_code
            new_auth_data['system_lang_code'] = system_lang_code
            new_auth_data['phone_code_hash'] = None
            new_auth_data['code'] = None
            new_auth_data['session_file'] = os.path.basename(new_session_file)
            new_auth_data['twoFA'] = new_two_fa
            new_auth_data['password'] = new_two_fa
            if not os.path.exists(new_sessions_dir):
                os.makedirs(new_sessions_dir)
            for attempt in range(max_retries):
                if not await ensure_connected(new_client, phone):
                    logger.error(f"Не удалось установить соединение для нового клиента для {phone}")
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
                            logger.info(f"Код авторизации для {phone} получен")
                            break
                        else:
                            logger.warning(f"Код не получен для {phone}, ожидание {retry_delay} секунд")
                            await asyncio.sleep(retry_delay)
                    if not new_auth_data['code']:
                        logger.error(f"Не удалось получить код для {phone}. Попробуйте ввести код вручную.")
                        new_auth_data['code'] = input(f"Введите код авторизации для {phone}: ")
                        if not new_auth_data['code']:
                            logger.error(f"Код не введен для {phone}")
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
                            logger.info(f"Успешная авторизация новой сессии для {phone} с 2FA")
                        except PasswordHashInvalidError:
                            logger.error(f"Неверный пароль 2FA для новой сессии {phone}: {new_two_fa}")
                            return False
                        except SessionPasswordNeededError:
                            logger.error(f"Требуется пароль 2FA для новой сессии {phone}, но он неверный: {new_two_fa}")
                            return False
                    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                        logger.error(f"Ошибка авторизации для {phone}: {e}. Код недействителен или истёк")
                        return False
                    except Exception as e:
                        logger.error(f"Ошибка при использовании кода для {phone}: {e}")
                        return False
                    if not await new_client.is_user_authorized():
                        logger.error(f"Не удалось авторизовать новую сессию для {phone}")
                        return False
                    for field in ['phone_code_hash', 'code']:
                        new_auth_data.pop(field, None)
                    save_auth_data(new_json_file, new_auth_data)
                    if LOGOUT_OLD_SESSION:
                        try:
                            await client.log_out()
                            logger.info(f"Старая сессия для {phone} успешно закрыта")
                            session_path = session_file + '.session'
                            if os.path.exists(session_path):
                                os.remove(session_path)
                                logger.info(f"Файл сессии {session_path} удален")
                            json_path = os.path.join(json_dir, json_filename + '.json')
                            if os.path.exists(json_path):
                                os.remove(json_path)
                                logger.info(f"JSON-файл {json_path} удален")
                        except Exception as e:
                            logger.error(f"Ошибка при logout старой сессии для {phone}: {e}")
                            return False
                    return True
                except FloodWaitError as e:
                    logger.error(f"Ограничение Telegram API для {phone}, требуется ожидание {e.seconds} секунд")
                    return False
                except AuthRestartError:
                    logger.error(f"Ошибка Telegram API для {phone}: AuthRestartError")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.error(f"Исчерпаны попытки для {phone}")
                        return False
                except Exception as e:
                    if "all available options for this type of number were already used" in str(e):
                        logger.error(f"Исчерпаны способы отправки кода для {phone}. Попробуйте позже.")
                        return False
                    logger.error(f"Ошибка при запросе кода для {phone}: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.error(f"Исчерпаны попытки для {phone}")
                        return False
        except AuthKeyUnregisteredError:
            logger.error(f"Сессия для {phone} не зарегистрирована")
            return False
        except Exception as e:
            logger.error(f"Ошибка при обработке сессии для {phone}: {e}")
            return False
        finally:
            if client.is_connected():
                await client.disconnect()
            if new_client.is_connected():
                await new_client.disconnect()

async def main():
    try:
        proxies = load_proxies()
    except Exception as e:
        return False, f"Не удалось загрузить прокси: {str(e)}"
    json_files = [f for f in os.listdir(json_dir) if f.endswith('.json')]
    if not json_files:
        return False, f"Не найдено JSON-файлов в {json_dir}"
    max_threads = load_config()
    semaphore = asyncio.Semaphore(max_threads)
    tasks = [process_session(os.path.join(json_dir, json_file), proxies, semaphore) for json_file in json_files]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = all(isinstance(result, bool) and result for result in results)
    if success:
        return True, "Все сессии успешно переавторизованы с новым 2FA"
    else:
        return False, "Некоторые сессии не были переавторизованы"

if __name__ == '__main__':
    asyncio.run(main())