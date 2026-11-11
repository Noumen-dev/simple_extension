# Mail & Excel Nodes

Компактный пакет нод для `noumen.workflow-orchestrator`: только чтение почты и сырое извлечение книг Excel. Бизнес-адреса, правила продуктов и маппинги в пакет не входят.

Включены только: **imap-read**, **excel-read**. Документация и интерфейсы находятся в `nodes/имя-ноды/`.

Репозиторий: `git@github.com:Noumen-dev/simple_extension.git`

Фабрика исполнения (Go-сервер): `git@github.com:Noumen-dev/base_fabric.git`

## Навыки агента

Расширение поставляет навык через `contributes.skills`:

- **writing-factory-node** — добавляет новую Python-ноду в kebab-case папку: обязательные поля `schema.json`, правила портов, класс с `@register_node_class`, регистрация в панели Noumen и запуск в workflow.

Инструкции навыка написаны на английском; агент отвечает на языке пользователя. Исходник лежит в `.fragmenta/skills/writing-factory-node/` и входит в пакет расширения. Правьте его здесь: сессионные копии Fragmenta пересоздаёт. Навык не создаёт подключения и не выдаёт права. В проектах с закреплённым списком расширений включите **Mail & Excel Nodes**; без такого списка Fragmenta может предоставлять навыки расширения и в общем чате.

## Сборка

Обновить зависимости Python нод: `python scripts/merge_node_requirements.py`.

Бинарь фабрики собирается в репозитории **base_fabric** и копируется сюда:

```sh
make -C ../server artifact   # из монорепо
# или
make fetch-binary            # из каталога extension/
```

Собрать контейнер расширения (свой `Dockerfile`, context = этот каталог):

```sh
make image
# или
docker build -t mail-excel-nodes:0.2.2 .
```

В пакет включён готовый Linux-сервер `gofactory-server-linux-amd64` (сборка с `-trimpath`). При изменениях Go пересоберите бинарь в **base_fabric** и снова скопируйте его в этот пакет. Для запуска контейнера используется `vault/env` с несекретными настройками; локальные секреты храните вне публикуемого пакета.

## Разработка нод

Обе ноды написаны на Python и вызываются фабрикой через `nodes/cli_runtime.py` (`--json`).

- **IMAP Read** — IMAPS read-only (`EXAMINE` / `BODY.PEEK[]`), MIME, вложения, SHA256, окна UID и candidate checkpoint. См. `nodes/imap-read/README.md`.
- **Excel Read** — XLS/XLSX/XLSM, сырые ячейки, формулы/кэши где доступны, merged ranges, provenance, ограниченный JSONL. См. `nodes/excel-read/README.md`.

Проверка пакета: `node scripts/check-mail-excel.mjs`. Регенерация схем: `node scripts/configure-mail-excel.mjs`.

Go-сервер — общий слой исполнения; для изменения только Python-нод его пересобирать не требуется. При старте сервер ищет Go/dotnet SDK **только если** в `nodes/` есть соответствующие native-ноды; в этом пакете их нет, поэтому SDK в runtime-образе не нужны. Node.js и TypeScript внутри образа отсутствуют.

## Формы и выражения в IDE

Fragmenta рисует правую панель и подсказывает выражения `{{Имя ноды.выход.путь}}` по `schema` в `nodes/*/schema.json`:

- объект с фиксированными ключами у входа или параметра (например, `credentials`) описан полной записью schema — в панели это форма, у каждого поля своя связь с другой нодой;
- выходы с известной формой описаны короткой записью — подсказка ведёт по ключам вглубь;
- вложенные `parameters` больше не используются: поля перенесены в schema, значение, которое получает нода, прежнее.

Ссылка на всё поле сохраняет тип значения, ссылка внутри текста подставляется строкой; форма, где связано хоть одно поле, уходит JSON-шаблоном и разворачивается в объект на прогоне.

## IMAP Read

Нода `imap-read` читает ящик только на чтение: credentials на входе, правила `{sender, subjects[]}`, bounded history и candidate checkpoint только после надёжной записи пакета. Подробности и границы лимитов — в `nodes/imap-read/README.md`.

## Excel Read

Нода `excel-read` извлекает книгу без бизнес-интерпретации и без выполнения макросов. Вход — file-объект фабрики или вложение из IMAP-архива; полный результат — `cells_file` (JSONL). Подробности — в `nodes/excel-read/README.md`.

## Проверка запуска контейнера

```sh
make fetch-binary
docker build -t mail-excel-nodes:local-check .
docker run --rm -p 7677:7677 mail-excel-nodes:local-check
# health: GET http://127.0.0.1:7677/health
```

Compose (сеть `orchestrator-network` должна существовать): `docker compose up --build`.
