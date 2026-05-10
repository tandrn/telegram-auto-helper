# Telegram-автопомощник на базе LLM

Учебный production-ready проект, который буферизует сообщения из Telegram, склеивает их в контекст, запрашивает черновик ответа у Yandex AI Studio и затем маршрутизирует результат по уровню риска. Бот работает в polling-режиме, запускается как `python bot.py` и не требует webhook.

Проект показывает полный цикл: сбор пачки сообщений, генерацию стилевого черновика, независимую проверку риска, ручное подтверждение владельцем и SQLite-логирование всех действий.

## Архитектура

```text
[Telegram] → [Telegram Bot API polling] → [bot.py на ВМ]
                                               ↓
                                    [MessageBuffer — буфер по chat_id]
                                    Ждёт 20 сек после последнего сообщения
                                               ↓
                                    [llm_client.py — generate_draft()]
                                    Yandex AI Studio API
                                               ↓
                                    [RiskRouter]
                                    LOW  → AUTO_SEND (отправить сразу)
                                    MEDIUM → DRAFT_MODE (показать черновик владельцу)
                                    HIGH → BLOCK (уведомить, не отправлять)
                                               ↓
                                    [logger.py → SQLite]
```

## Стек

| Компонент | Библиотека | Назначение |
| --- | --- | --- |
| Telegram polling | aiogram 3.x | Прием сообщений и callback-кнопок |
| LLM-клиент | openai | AsyncOpenAI для Yandex AI Studio |
| Конфигурация | python-dotenv | Загрузка переменных из .env |
| Логи в БД | aiosqlite | Асинхронная запись в SQLite |
| Аналитика | pandas | Просмотр логов и демонстрационных таблиц |
| Notebook | jupyterlab | Демонстрация без реального Telegram |

## Быстрый старт на локальной машине (Ubuntu/Linux/macOS)

1. Создайте виртуальное окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Установите зависимости:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

3. Подготовьте переменные окружения:

```bash
cp .env.example .env
```

4. Заполните .env значениями Yandex и Telegram.

5. Запустите бота в foreground для проверки:

```bash
python bot.py
```

## Развёртывание на ВМ Ubuntu 24 (production)

### Подготовка

1. Подключитесь к ВМ по SSH.
2. Установите Python 3.11+ и git:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git
```

3. Клонируйте проект:

```bash
cd /opt
sudo git clone <ваш-репозиторий> telegram-auto-helper
cd telegram-auto-helper/project10
```

4. Создайте вирт. окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

5. Установите зависимости:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

6. Подготовьте .env с реальными ключами:

```bash
cp .env.example .env
nano .env  # заполните значения
```

7. Проверьте запуск вручную:

```bash
python bot.py
```

Если логирует сообщения и не падает — всё готово. Прервите Ctrl+C.

### Автозапуск через systemd

1. Создайте service-файл:

```bash
sudo nano /etc/systemd/system/telegram-auto-helper.service
```

2. Вставьте содержимое (замените `/opt/telegram-auto-helper/project10` на вашу папку):

```ini
[Unit]
Description=Telegram Auto-Helper LLM Bot
After=network.target

[Service]
Type=simple
User=<ваш-пользователь>
WorkingDirectory=/opt/telegram-auto-helper/project10
Environment="PATH=/opt/telegram-auto-helper/project10/.venv/bin"
ExecStart=/opt/telegram-auto-helper/project10/.venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

3. Включите и запустите:

```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-auto-helper.service
sudo systemctl start telegram-auto-helper.service
```

4. Проверьте статус:

```bash
sudo systemctl status telegram-auto-helper.service
journalctl -u telegram-auto-helper.service -f  # смотреть логи в реальном времени
```

### Развёртывание через Docker

Если на ВМ установлен Docker:

```bash
cd /opt/telegram-auto-helper/project10
docker build -t telegram-auto-helper .
docker run -d --restart unless-stopped --env-file .env --name telegram-auto-helper telegram-auto-helper
```

Проверка:

```bash
docker logs -f telegram-auto-helper
```

Остановка:

```bash
docker stop telegram-auto-helper
docker rm telegram-auto-helper
```

## Настройка .env

- YANDEX_API_KEY и YANDEX_FOLDER_ID берутся из Yandex Cloud / Yandex AI Studio.
- YANDEX_MODEL можно оставить по умолчанию: qwen2.5-72b-instruct.
- TELEGRAM_BOT_TOKEN берется у BotFather.
- OWNER_CHAT_ID — это числовой Telegram ID владельца, которому бот отправляет черновики на подтверждение. Удобный способ узнать его — написать @userinfobot и взять numeric id.

## Команды бота

- /start — приветствие и краткое описание режимов.
- /setup — сбор 10 примеров сообщений и генерация стилевого профиля.
- /log — последние записи из SQLite.
- /status — текущий стилевой профиль и настройки.

## Режимы ответа

| Условие | Действие | Комментарий |
| --- | --- | --- |
| LOW и should_autosend=true | AUTO_SEND | Перед отправкой идет risk_check |
| MEDIUM или should_autosend=false | DRAFT | Черновик отправляется владельцу с кнопками |
| HIGH | BLOCK | Собеседнику ничего не отправляется |

## Где используется LLM

- generate_draft — собирает черновик ответа, уровень риска, причину риска, тон и summary входящего.
- analyze_style — строит стилевой профиль владельца по примерам сообщений.
- risk_check — независимая проверка перед автосендом.

## Пример диалога

Входящая пачка:

Привет
Слушай
Можешь занять 5000 до пятницы?

LLM возвращает:

{
  "draft": "Прости, сейчас не могу",
  "risk_level": "HIGH",
  "risk_reason": "Финансовая просьба — максимальный риск в пачке",
  "should_autosend": false,
  "tone_used": "нейтральный",
  "context_summary": "Просьба занять деньги"
}

RiskRouter отправляет владельцу блокировку и не пересылает черновик собеседнику.

## Проблемы с сетевой конфигурацией

Если LLM и Telegram требуют разных маршрутов доступа:

**Вариант 1: Yandex LLM через прокси, Telegram через другой прокси или VPN**
- Установите `YANDEX_PROXY_URL` для маршрутизации запросов LLM через прокси-сервер.
- Установите `TELEGRAM_PROXY_URL` для маршрутизации трафика Telegram.
- Оба параметра читаются из .env и автоматически применяются при запуске бота.

**Вариант 2: Yandex через VPN, Telegram через прокси**
- Включите VPN и оставьте `YANDEX_PROXY_URL` пустым — LLM будет работать через системный маршрут.
- Заполните `TELEGRAM_PROXY_URL` для Telegram.

Примеры значений:
- HTTP-прокси: `http://proxy.example.com:8080`
- SOCKS5-прокси: `socks5://proxy.example.com:1080`
- Yandex Proxy URL в формате из примера .env: скопируйте значение из текущей конфигурации.

## Безопасность

- Не публикуйте .env, токены Telegram, API-ключи и YANDEX_FOLDER_ID.
- Не храните секреты в ноутбуке и README.
- OWNER_CHAT_ID должен принадлежать только владельцу аккаунта.
- Не включайте автосенд для непроверенных сценариев без risk_check.

## Ограничения и улучшения

- Сейчас буфер привязан к chat_id; для групп можно добавить более точную сегментацию по отправителю.
- Для production можно вынести хранение черновиков и состояний владельца в Redis.
- Можно расширить risk_check отдельными правилами или локальной эвристикой до запроса к LLM.
- Для больших нагрузок полезно добавить ретраи HTTP и circuit breaker.

## Проверка перед сдачей

1. Убедиться, что файл .env заполнен и не попадает в репозиторий.
2. Проверить сборку Docker-образа командой:

docker build -t telegram-auto-helper .

3. Проверить локальный запуск в виртуальном окружении:

python bot.py

4. Прогнать сценарий /start, затем /status, затем /setup с 10 примерами.
5. Отправить тестовую пачку из 2-3 сообщений и убедиться, что срабатывает буфер.
6. Проверить /log и наличие записи в SQLite после теста.
