# Triage Bugs Tool

Автономный инструмент для триажа багов из Jira: берёт баг-тикеты, находит их спецификацию
(User Story) в Confluence и просит Gemini классифицировать severity/impact/priority и решить,
реальный ли это баг. Работает в режиме dry-run (только предпросмотр) или apply (реально
пишет в Jira).

Это выделенная часть проекта **Trace2Quality** — только функционал Triage Bugs и всё, от чего
он зависит (без Azure DevOps, генерации тест-кейсов, coverage-анализа и прочего).

## Требования

- **Python 3.11+** (код использует синтаксис `X | None`, на Python 3.9/3.10 не запустится).
  На macOS системный `python3` часто указывает на старую версию (3.9) — `make setup`
  сам найдёт `python3.11`/`python3.12`/`python3.13`, если он есть в PATH (например,
  после `brew install python@3.11`); если такого интерпретатора нет, `make setup`
  остановится с понятной ошибкой вместо непонятного краха.
- Учётки с доступом к Jira Cloud, Confluence Cloud и Google Gemini API
- Никаких внешних сервисов (Redis, Postgres, Docker) не требуется — всё работает на
  локальном venv + SQLite

## Быстрый старт

```bash
git clone <URL этого репозитория>   # или распакуйте архив
cd triage-bugs-tool

make setup      # создаст venv, поставит зависимости, скопирует .env.example -> .env
make dev        # запустит приложение на http://localhost:8000
```

Откройте **http://localhost:8000** — попадёте на дашборд.

Если `make` недоступен (например, Windows без WSL), эквивалентные команды:

```bash
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -e .
cp .env.example .env
mkdir -p data/artifacts
PYTHONPATH=$(pwd) venv/bin/uvicorn apps.app.main:app --reload --port 8000
```

## Настройка учётных данных

Есть два равнозначных способа — используйте любой (второй удобнее для тестирования
разными пользователями на одной инсталляции, так как хранится в БД, а не в файле):

### Вариант A — через `.env`

Откройте `.env` и заполните:

```
CONFLUENCE_BASE_URL=https://yourdomain.atlassian.net/wiki
CONFLUENCE_SPACE=YOURSPACE
CONFLUENCE_EMAIL=your.email@domain.com
CONFLUENCE_API_TOKEN=...

JIRA_BASE_URL=https://yourdomain.atlassian.net
JIRA_EMAIL=your.email@domain.com
JIRA_API_TOKEN=...

GEMINI_API_KEY=...
```

Для Jira Cloud и Confluence Cloud на одном Atlassian-домене **email и API-токен обычно
одинаковые** — можно один раз создать токен и вставить его в оба поля.

- **Jira/Confluence API token**: https://id.atlassian.com/manage-profile/security/api-tokens
  → «Create API token».
- **Gemini API key**: https://aistudio.google.com/apikey

После правки `.env` перезапустите `make dev`.

### Вариант B — через веб-интерфейс

Откройте **http://localhost:8000/ui/integrations** — там три карточки (Jira, Confluence,
Gemini). Заполните поля, нажмите **Save**, затем **Test Connection**, чтобы убедиться, что
всё настроено верно. Значения хранятся в SQLite зашифрованными (Fernet, ключ —
`APP_ENCRYPTION_KEY` в `.env`) и никогда не отображаются обратно в браузере.

Конфиг из БД (вариант B) имеет приоритет над `.env` (вариант A) для каждого провайдера.

## Как пользоваться

1. Откройте **http://localhost:8000/ui/workflows/triage_bugs/run**.
2. Задайте JQL-фильтр (по умолчанию отбирает баги в статусе `Backlog`), лимит количества,
   ID кастомных полей Severity/Impact в вашей Jira и целевой статус после триажа.
3. Оставьте чекбокс **Apply** не отмеченным и нажмите **Run Triage** — запустится dry-run:
   ничего не пишется в Jira, только показывается вердикт Gemini по каждому багу
   (реальный баг или нет, severity/impact/priority, обоснование).
4. Просмотрите таблицу результатов на странице мониторинга. Для каждого бага с
   вердиктом «реальный» доступна кнопка **Apply** — применить именно этот баг, либо
   **«Apply All Real Bugs to Jira»** — применить все разом. Кнопка добавления
   комментария в Jira — опциональна.
5. ID кастомных полей Severity/Impact в вашей Jira можно найти через
   `GET /rest/api/3/issue/createmeta` вашего Jira-инстанса, либо через администрирование
   полей в Jira.

### Как найти ID кастомных полей Jira

```
curl -u "email:api_token" \
  "https://yourdomain.atlassian.net/rest/api/3/field" | jq '.[] | select(.name | test("Severity|Impact"; "i"))'
```

## Структура проекта

```
apps/app/
  main.py          — точка входа FastAPI, регистрация роутеров и клиентов интеграций
  config.py        — настройки (.env)
  database.py      — модели SQLAlchemy (SQLite, таблицы создаются автоматически при старте)
  workflows.py     — исполнитель workflow triage_bugs (фоновый поток, без Celery/Redis)
  api/             — JSON REST: /api/integrations, /api/workflows, /api/runs, /api/artifacts
  ui/              — серверный HTML-интерфейс: dashboard, workflows, runs, integrations
packages/
  common/          — общие Pydantic-модели, логирование, шифрование секретов
  integrations/    — клиенты Jira, Confluence, Gemini (httpx / google-genai)
  workflows/triage/ — сама логика триажа: поиск US в Confluence, оценка Gemini, апдейт Jira
```

## Отличия от полного Trace2Quality

Это упрощённый, самодостаточный срез — вот что сознательно убрано:

- Интеграция с Azure DevOps и всё, что от неё зависит (Test Plans, coverage-анализ,
  генерация тест-кейсов, CSV Fixer, orphan test cases и т.д.).
- Celery/Redis — фоновые задачи выполняются в обычном Python-потоке в рамках процесса
  приложения (для одного пользователя этого достаточно).
- Alembic-миграции — таблицы SQLite создаются автоматически при старте приложения
  (`Base.metadata.create_all`), отдельный шаг миграции не нужен.
- Composition/scheduling/webhooks — многошаговые цепочки workflow и cron-расписания.

Если нужен полный функционал (coverage, генерация тест-кейсов из Confluence и т.д.) —
обращайтесь к полному репозиторию Trace2Quality.

## Безопасность

- Все секреты (API-токены, ключи) хранятся в БД зашифрованными через Fernet
  (`APP_ENCRYPTION_KEY` в `.env`). **Установите свой ключ** перед реальным использованием —
  сгенерировать можно так:
  ```bash
  venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Не коммитьте `.env` и файл `data/triage_bugs.db` (они уже в `.gitignore`) — там могут
  оказаться реальные токены и данные багов.
- Apply-действия (запись severity/impact/priority и переход статуса в Jira) выполняются
  под учёткой, чей API-токен указан в настройках Jira — Jira атрибутирует изменения
  именно этому аккаунту.
