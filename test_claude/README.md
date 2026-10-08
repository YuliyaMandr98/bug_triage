# test_claude — изолированный тест триажа через Claude (Anthropic API)

Полностью отдельная песочница внутри проекта: проверяет, работает ли ваш Anthropic
(Claude Code) API-ключ для той же самой задачи, что и Gemini в основном приложении
(`triage_bugs`), **не меняя ни одного файла основного приложения**.

## Как это устроено

- `anthropic_client.py` — независимый мини-клиент Claude (не связан с
  `packages/integrations/`, нигде не регистрируется в `integration_registry`
  основного приложения). Реализует ровно тот же промпт и контракт ответа, что
  `GeminiClient.assess_bug()` в `packages/integrations/gemini/__init__.py` — для
  честного сравнения "как отработал бы Gemini vs как отработал Claude".
- `triage_via_claude.py` — CLI-скрипт: подключается к вашим реальным Jira/Confluence
  (через уже существующие `JiraClient`/`ConfluenceClient` — этот код не менялся) и
  прогоняет `packages.workflows.triage.run_triage_bugs_workflow(...)`, передав туда
  `AnthropicClient` вместо `GeminiClient`. Сама функция workflow ничего не знает и не
  должна знать, что ей передали не Gemini — она просто вызывает `.assess_bug(...)`.
- **Всегда dry-run** — скрипт никогда не пишет в Jira, независимо от флагов. Это
  инструмент сравнения моделей, а не замена реального workflow.

Ничто вне этой папки не изменено и не импортирует файлы из неё — можно спокойно
удалить `test_claude/` целиком, ничего в основном приложении не сломается.

## Настройка

1. Установите Anthropic SDK в тот же venv, что использует проект:
   ```bash
   venv/bin/pip install anthropic
   ```
2. Добавьте в **тот же** `.env`, что уже использует основное приложение (он и так
   игнорирует незнакомые переменные — см. `apps/app/config.py`, `extra="ignore"`):
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ANTHROPIC_MODEL=claude-sonnet-5   # необязательно, это значение по умолчанию
   ```
   `CONFLUENCE_*` и `JIRA_*` должны уже быть заполнены — скрипт использует их
   как есть, из того же `.env`.

## Запуск

```bash
PYTHONPATH=$(pwd) venv/bin/python test_claude/triage_via_claude.py
PYTHONPATH=$(pwd) venv/bin/python test_claude/triage_via_claude.py --jql "status = Backlog" --max-results 5
```

Результат — в `test_claude/data/triage_result_claude.json` (путь можно
переопределить через `--output`) и в выводе терминала: по каждому багу — реальный ли
он, по мнению Claude, severity/impact/priority и обоснование. Сравните с тем, что
показывает `/ui/runs` для реальных Gemini-прогонов того же JQL.

## Известные ограничения

- Проверялось на установленной версии `anthropic` SDK (1.3.0). Если у вас
  установится другая версия и `messages.create(...)` откажется работать — проверьте
  сигнатуру: `import anthropic; help(anthropic.AsyncAnthropic().messages.create)`.
- Ретраи на rate-limit/перегрузку сделаны по аналогии с `GeminiClient`
  (`_generate_with_retry`), но не настолько же протестированы в реальных условиях —
  при частых 429/529 увеличьте `--batch-delay-seconds`.
