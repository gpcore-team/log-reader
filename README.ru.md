# GPCORE Log Reader

[EN version](README.md)

Универсальный фильтр логов доступа Caddy. Читает JSON-lines лог инкрементально, отбирает записи по настраиваемым ключевым словам, обогащает их геолокацией и информацией о способе загрузки, пишет результат в отдельный нумерованный лог. Поддерживает верификацию ботов (Google, Yandex, Bing, Apple, DuckDuckGo), конфигурацию через профили и автоматическое обновление GeoIP-базы.

Изначально написан для [GPCORE](https://gpcore.ru) — сообщества IW4x, но подходит для любого мониторинга загрузок файлов, раздаваемых через Caddy.

## Возможности

- **Инкрементальное чтение.** Позиция (offset) сохраняется на диск; каждый запуск обрабатывает только новые строки.
- **Конфигурация через профили.** Один движок, много профилей в `profiles/*.toml`.
- **Обогащение данных.**
  - Код страны из GeoIP (GeoLite2-City, скачивается и обновляется автоматически).
  - Человекочитаемая длительность (`2m 40s`, `1h 2m 5s`).
  - Скорость загрузки в MB/s.
  - Трансформация имени файла через regex (короткие имена релизов).
- **Детекция верифицированных ботов.**
  - Двухшаговая проверка: `is-crawler` по User-Agent, затем forward-confirmed reverse DNS по IP.
  - Google, Yandex, Bing, Apple, DuckDuckGo.
  - Верифицированные боты пишутся в отдельный лог, в статистику загрузок не попадают.
- **Список игнорируемых IP.** Пропуск собственных отладочных запросов.
- **Нумерованный вывод** с накопительной статистикой между запусками.
- **Автоматическое обновление GeoLite2** раз в 30 дней.
- **Совместимость с logrotate** (через `copytruncate`).

## Быстрый старт

    git clone https://github.com/gpcore-team/log-reader.git
    cd log-reader
    ./install.sh

Затем создайте профиль:

    cp /opt/log_reader/profiles/example.toml /opt/log_reader/profiles/mysite.toml
    nano /opt/log_reader/profiles/mysite.toml

И запустите:

    python3 /opt/log_reader/caddy_filter.py --profile mysite

## Требования

- **ОС:** Debian/Ubuntu (тестировалось на Ubuntu 20.04 LTS). Работает на любом современном Linux.
- **Python:** 3.8 или новее.
  - Python 3.11+ использует встроенный `tomllib`.
  - Python 3.8–3.10 требует `tomli` (устанавливается через `install.sh`).
- **Caddy**, настроенный писать access-логи в JSON-формате.
- **pip3** для установки зависимостей.
- **Опционально:** `logrotate` (для ротации логов), пакет `acl` (для выдачи прав на чтение логов Caddy непривилегированному пользователю).

## Установка

### Автоматическая

Скрипт `install.sh` делает всё:

    ./install.sh --help                                # показать опции
    ./install.sh                                       # интерактивный режим
    ./install.sh --profile mysite --with-cron --with-logrotate

Опции:

| Флаг | Значение |
|---|---|
| `--install-dir DIR` | Директория установки (по умолчанию `/opt/log_reader`) |
| `--profile NAME` | Имя профиля для cron-задачи |
| `--with-cron` | Добавить cron-задачу (каждые 5 минут) |
| `--with-logrotate` | Установить конфиг logrotate в `/etc/logrotate.d/log-reader` |
| `--skip-deps` | Не устанавливать Python-зависимости |
| `--non-interactive` | Пропустить все вопросы, использовать значения по умолчанию |

Скрипт идемпотентен — повторный запуск безопасен.

### Ручная

Если предпочитаете вручную:

    # 1. Зависимости
    sudo apt install python3-pip
    pip install --user tomli geoip2 is-crawler

    # 2. Создание структуры
    sudo mkdir -p /opt/log_reader
    sudo chown "$USER":"$USER" /opt/log_reader
    mkdir -p /opt/log_reader/{log,state,backup,profiles}

    # 3. Копирование файлов
    cp caddy_filter.py stats.py bots_view.py reset.sh backup.sh \
       help.txt debug.txt logrotate.conf /opt/log_reader/
    chmod 755 /opt/log_reader/{caddy_filter.py,stats.py,bots_view.py,reset.sh,backup.sh}

    # 4. Доступ к логам Caddy (замените 'kn' на вашего пользователя)
    sudo setfacl -m u:kn:r-x /var/log/caddy
    sudo setfacl -m u:kn:r-- /var/log/caddy/access.log
    sudo setfacl -d -m u:kn:r-- /var/log/caddy

    # 5. Создание профиля
    cp /opt/log_reader/profiles/example.toml /opt/log_reader/profiles/mysite.toml
    nano /opt/log_reader/profiles/mysite.toml

## Конфигурация

Профили лежат в `/opt/log_reader/profiles/*.toml`. Файл `example.toml` полностью документирован — скопируйте и отредактируйте.

Минимальный профиль:

    [profile]
    name        = "My site downloads"
    site        = "example.com"
    input_log   = "/var/log/caddy/access.log"
    keywords    = ["myfile.zip"]
    output_log  = "log/mysite_dl.log"
    offset_file = "state/mysite_filter_offset"

Все пути относительны `/opt/log_reader/`, если не начинаются с `/`. Плейсхолдер `{site}` разворачивается в путях-шаблонах.

### Доступные плейсхолдеры

В `output_format`:

| Плейсхолдер | Значение |
|---|---|
| `{ts}` | Дата и время `YYYY-MM-DD HH:MM:SS` |
| `{country}` | Код страны (`RU`, `US`, `??`) |
| `{ip}` | IP-адрес клиента |
| `{duration}` | Человекочитаемая длительность (`45s`, `2m 40s`, `1h 2m 5s`) |
| `{speed}` | Скорость загрузки (`3.58 MB/s` или `—`) |
| `{filename}` | Имя файла (возможно трансформированное) |
| `{uri}` | Полный URI запроса |
| `{size}` | Размер ответа в байтах |
| `{status}` | HTTP-код ответа |
| `{ua_tag}` | Классификация UA: `UPD1.2`, `PSH` или пусто |

Работают спецификаторы формата: `{ip:<16}`, `{country:>3}`.

### Трансформация имени файла

Для сокращения длинных имён через regex:

    [filename_transform]
    mode    = "short"
    pattern = "r(\\d+)_rawfiles_(v[\\d.]+)\\.exe"
    result  = "{1}_{2}"

Для файла `IW4x_Update_r5154_rawfiles_v0.2.36.exe` результат — `r5154_v0.2.36`.

Если `mode = "full"` или regex не совпал — используется basename как есть.

## Использование

    # Список профилей
    python3 /opt/log_reader/caddy_filter.py --list

    # Ручной запуск профиля
    python3 /opt/log_reader/caddy_filter.py --profile mysite

    # Статистика (по профилю)
    python3 /opt/log_reader/stats.py --profile mysite
    python3 /opt/log_reader/stats.py --profile mysite --verbose

    # Просмотр лога ботов
    python3 /opt/log_reader/bots_view.py /opt/log_reader/log/bots_example.com.log
    python3 /opt/log_reader/bots_view.py /opt/log_reader/log/bots_example.com.log 100
    python3 /opt/log_reader/bots_view.py /opt/log_reader/log/bots_example.com.log 0

    # Полный сброс (очищает логи, состояние, пересобирает с нуля)
    /opt/log_reader/reset.sh --profile mysite

    # Сброс всех профилей
    /opt/log_reader/reset.sh --profile_all

    # Бэкап
    /opt/log_reader/backup.sh

## Cron

Добавьте задачу для каждого профиля:

    */5 * * * * /usr/bin/python3 /opt/log_reader/caddy_filter.py --profile mysite

Обратите внимание: редирект через `>>` не нужен — вывод идёт в `cron_log`, указанный в профиле.

## Logrotate

В комплекте идёт `logrotate.conf` с `copytruncate`, потому что фильтр держит лог-файл открытым в режиме append. Установить:

    ./install.sh --with-logrotate

или вручную:

    sudo cp logrotate.conf /etc/logrotate.d/log-reader
    # Замените @INSTALL_DIR@ и 'su USER GROUP' на реальные значения.
    sudo logrotate -d /etc/logrotate.d/log-reader

## Архитектура

    /opt/log_reader/
    |-- caddy_filter.py           универсальный движок
    |-- stats.py                  просмотр статистики
    |-- bots_view.py              просмотр лога ботов
    |-- reset.sh                  сброс профиля
    |-- backup.sh                 бэкап через tar --zstd
    |-- help.txt                  краткая справка (алиасы)
    |-- debug.txt                 расширенная справка по отладке
    |-- logrotate.conf            шаблон конфига ротации
    |
    |-- profiles/                 конфиги профилей
    |   `-- example.toml
    |
    |-- log/                      runtime-логи
    |   |-- <profile>_dl.log
    |   |-- <profile>_cron.log
    |   `-- bots_<site>.log
    |
    |-- state/                    состояние профилей
    |   |-- <profile>_filter_offset
    |   |-- <profile>_filter_stats.json
    |   `-- <profile>_bots_cache.json
    |
    |-- backup/                   архивы tar.zst
    |
    `-- GeoLite2-City.mmdb        общая geo-база

## Как это работает

1. **Чтение** новых строк из access-лога Caddy начиная с последней сохранённой позиции.
2. **Парсинг** каждой строки как JSON. Извлекаются `request.uri`, `request.remote_ip`, `request.headers.User-Agent`, `duration`, `size`, `status`, `ts`.
3. **Фильтрация** по ключевым словам (подстрока в URI).
4. **Пропуск** IP из ignore-списка.
5. **Верификация ботов** если в профиле задан `bots_log`:
   - `is-crawler` сверяет User-Agent с базой из ~1200 паттернов.
   - Если это краулер — forward-confirmed reverse DNS валидирует IP.
   - Верифицированный бот пишется в `bots.log`.
6. **Обогащение** записи: страна по GeoIP, человекочитаемая длительность, скорость, трансформированное имя файла, метка UA.
7. **Форматирование** через `output_format` с подстановкой плейсхолдеров.
8. **Запись** в output-лог (опционально с нумерацией) и обновление накопительной статистики.
9. **Сохранение** offset, статистики и кэша ботов.

## Автообновление GeoLite2

База GeoLite2 скачивается при первом запуске и обновляется раз в 30 дней. URL зашит в `caddy_filter.py`:

    GEOLITE_DB_URL = "https://cdn.jsdelivr.net/npm/geolite2-city/GeoLite2-City.mmdb.gz"

Если скачивание не удалось, но старая база валидна — фильтр продолжает работу со старой и пишет предупреждение.

Принудительное обновление:

    rm /opt/log_reader/GeoLite2-City.mmdb
    python3 /opt/log_reader/caddy_filter.py --profile mysite

## Верифицированные боты

Детекция состоит из двух шагов:

1. **Проверка User-Agent** через [is-crawler](https://pypi.org/project/is-crawler/).
2. **Forward-confirmed reverse DNS** (FCrDNS):
   - Обратный DNS по IP -> hostname.
   - Прямой DNS по hostname -> должен резолвиться в тот же IP.
   - Hostname должен заканчиваться на доверенный суффикс (`.googlebot.com`, `.yandex.ru` и т.д.).

Это защищает от подделки User-Agent: даже если кто-то выставит `User-Agent: Googlebot`, он не контролирует обратный DNS своего IP.

Доверенные суффиксы задаются в `caddy_filter.py`:

    TRUSTED_RDNS_SUFFIXES = (
        ".googlebot.com", ".google.com",
        ".search.msn.com",
        ".yandex.ru", ".yandex.net", ".yandex.com",
        ".applebot.apple.com", ".apple.com",
        ".duckduckgo.com",
    )

## Диагностика

Подробный список диагностических команд — в файле `debug.txt`.

Частые проблемы:

- **`ModuleNotFoundError: tomli`** — установите: `pip3 install --user tomli` (только для Python < 3.11).
- **`Permission denied` на логе Caddy** — выдайте ACL: `sudo setfacl -m u:$USER:r-- /var/log/caddy/access.log`.
- **Пустой output-лог** — проверьте, что ключевые слова совпадают с URI в логе:
      grep 'your-keyword' /var/log/caddy/access.log | head
- **Logrotate ругается на "insecure permissions"** — убедитесь, что `su USER GROUP` в `/etc/logrotate.d/log-reader` соответствует фактическому владельцу директории установки.

## Вклад

Багрепорты и пул-реквесты приветствуются: [github.com/gpcore-team/log-reader](https://github.com/gpcore-team/log-reader).

## Лицензия

Apache License 2.0 — см. [LICENSE](LICENSE).
