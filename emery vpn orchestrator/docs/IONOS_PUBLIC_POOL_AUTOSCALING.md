# Skryon: общий VPN-пул и автоматическое масштабирование

Статус документа: реализованный технический контракт от 2026-08-10. Backend
умеет безопасно запросить покупку и bootstrap через внешний идемпотентный
provider-adapter. Прямые недокументированные запросы сайта IONOS в код не
добавлены: фактическая трата денег включается только после установки проверенного
`NODE_PROVISION_SCRIPT`, лимитов бюджета и явного `AUTO_PROVISION_ENABLED=true`.

## Зафиксированные правила продукта

- Пул общедоступный: тариф и заказ не дают пользователю «личный VPS». Для
  admission control backend внутренне назначает устройство на один узел; это
  назначение не видно как владение сервером и не освобождается пользователем.
- Один зарегистрированный Android-девайс занимает одно место.
- Лимиты тарифов: `Личный = 1`, `Личный+ = 2`, `Семейный = 5`.
- Пользователь не может отвязать устройство и повторно использовать место.
- Новый код тарифа активируется в разделе «Расширенные». Старый локальный профиль
  заменяется только после успешной проверки и сохранения нового VLESS-профиля.
- Первый успешный вход устройства заполняет `used_at`. После этого checkout
  помечает заказ как технически не подходящий для автоматического возврата.
- Сервер рассчитан на 20 зарегистрированных устройств и 30 Мбит/с на устройство.
  Расчётный худший трафик узла: `20 × 30 = 600 Мбит/с`.
- В каждом регионе сохраняется резерв в пять мест — размер полного семейного
  тарифа. После назначения 16-го устройства остаётся четыре места, и регион
  получает запрос на ещё один сервер.
- Полный узел (`20/20`) не участвует в выборе для нового устройства.
- Автомасштабирование только добавляет серверы. Оно никогда автоматически не
  удаляет VPS и не вызывает deprovision.

## Последовательность масштабирования

1. Legacy API внутри `BEGIN IMMEDIATE` проверяет тарифный лимит и запрашивает
   `prepare` по HMAC-псевдониму. Сырой код и Android ID в pool backend не уходят.
2. Pool backend условным `UPDATE ... current_clients < capacity_clients`
   резервирует ровно одно место, создаёт UUID и выделенный TCP-порт.
3. Через SSH или `XRAY_CREDENTIAL_SCRIPT` UUID атомарно добавляется в Xray,
   общий UUID отключается, nftables ограничивает порт до 30 Мбит/с, а Xray
   блокирует SMTP и private networks. Только после успешной проверки ссылка
   возвращается приложению.
4. Legacy API сохраняет ссылку в своей транзакции и вызывает `confirm`. Брошенная
   неподтверждённая бронь автоматически удаляется после TTL.
5. При выборе региона устройство назначается на самый свободный здоровый узел.
6. Capacity controller пересчитывает свободные места региона.
7. Если свободно меньше пяти мест и нет узла со статусом `draft`,
   `provisioning` и нет временно восстанавливаемого узла, создаётся один запрос
   на расширение. Сбой существующего VPS не считается нехваткой ёмкости и не
   запускает случайную покупку замены.
8. Перед заказом применяются предохранители: явное включение, разрешённый
   provider, один незавершённый запрос на регион, лимит за час, лимит за сутки и
   месячный бюджет.
9. После покупки выполняются bootstrap Debian, установка Xray, создание Reality,
   healthcheck и только затем перевод узла в `active`.
10. Пока узел не `active + healthy/degraded`, пользователи его не видят.

Параметры окружения:

```dotenv
POOL_NODE_CAPACITY_DEVICES=20
POOL_FAMILY_HEADROOM_DEVICES=5
POOL_NODE_BANDWIDTH_MBPS=600
POOL_PER_DEVICE_SPEED_LIMIT_MBPS=30
POOL_ACCOUNTING_BRIDGE_ENABLED=false
UNIQUE_DEVICE_CREDENTIALS_ENABLED=false
PER_DEVICE_RATE_LIMIT_ENFORCED=false
SMTP_ABUSE_PROTECTION_ENABLED=false
POOL_BRIDGE_API_KEY=<shared-random-secret>
POOL_ASSIGNMENT_PREPARE_TTL_SECONDS=300
POOL_ASSIGNMENT_MAINTENANCE_INTERVAL_SECONDS=60
XRAY_CLIENT_PORT_START=20000
XRAY_CLIENT_PORT_END=20199
XRAY_CREDENTIAL_SCRIPT=

AUTO_PROVISION_ENABLED=false
AUTO_PROVISION_PROVIDER=unconfigured
NODE_PROVISION_SCRIPT=
NODE_PROVISION_SCRIPT_TIMEOUT_SECONDS=900
AUTO_PROVISION_SERVER_MONTHLY_COST_EUR=0
AUTO_PROVISION_MAX_SERVERS_PER_HOUR=1
AUTO_PROVISION_MAX_SERVERS_PER_DAY=2
AUTO_PROVISION_MONTHLY_BUDGET_EUR=0
AUTO_PROVISION_RETRY_SECONDS=300

AUTO_RENEWAL_ACTIONS_ENABLED=false
NODE_RENEWAL_SCRIPT=
RENEWAL_PLANNING_HORIZON_DAYS=14
```

Нулевой бюджет и `AUTO_PROVISION_ENABLED=false` — обязательное безопасное
состояние по умолчанию. Для IONOS используется provider `ionos_vps_plus`, но он
разрешается guardrail только при настроенном внешнем адаптере. Наличие одного
имени provider никогда не запускает покупку.

## Атомарный мост между двумя backend

Регистрация Android-устройств выполняется legacy-сервисом `orchestrator/`
на `skryon.ru`, а таблица `vpn_nodes` и capacity controller находятся в
`emery vpn orchestrator/` на отдельной БД. Сейчас между ними синхронизируется
каталог серверов. Теперь между ними также работает двухфазный bridge:

- `POST /api/v1/internal/pool/assignments/prepare`;
- `POST /api/v1/internal/pool/assignments/confirm`;
- `POST /api/v1/internal/pool/assignments/maintenance`.

Все методы защищены отдельным `X-Pool-Bridge-Key`. `subject_key` и
`entitlement_hash` — 64-символьные HMAC, поэтому pool backend не получает
activation code и идентификатор устройства. Повторный `prepare` возвращает то же
назначение; ошибка ёмкости имеет HTTP 409 и откатывает локальную регистрацию.

## IONOS: подтверждённые факты и блокер

На официальной странице IONOS VPS L+ указан как 6 vCPU, 8 ГБ RAM, 240 ГБ NVMe,
5 €/месяц первые три месяца и 18 €/месяц по обычной цене. VPS+ включает
неограниченный трафик до 1 Гбит/с:

- <https://www.ionos.de/server/vps>

Публичная документация Cloud Panel API перечисляет Cloud Server, Dedicated и
Bare Metal, но не VPS+. Официальное сравнение отдельно указывает API для Cloud
Server и отсутствие API у VPS:

- <https://www.ionos.de/hilfe/server-cloud-infrastructure/cloud-panel-verwaltung/api/api-allgemeine-informationen/>
- <https://www.ionos.de/hilfe/server-cloud-infrastructure/allgemeine-informationen-vps/unterschiede-zwischen-cloud-server-und-vps/>

Поэтому нельзя безопасно реализовать покупку дешёвого VPS+ через недокументированные
запросы сайта или browser automation. Нужен письменный ответ IONOS с endpoint,
типом токена, idempotency key, способом заказать дополнительную VM, чтением её IP,
ценой продления и правилами коммерческого VPN.

IONOS Cloud API технически автоматизируется, но это другой продукт с другой
тарификацией трафика; подменять им VPS+ без нового расчёта экономики нельзя.

## Экономика VPS L+ при текущих ценах

При полной обычной цене 18 € и условном курсе 100 ₽/€ один VPS стоит около
1 800 ₽/месяц без control plane, эквайринга, налогов, поддержки и резерва.

| Заполнение 20 мест | Выручка | Наценка только на VPS |
|---|---:|---:|
| 20 × Личный по 200 ₽ | 4 000 ₽ | 122% |
| 10 × Личный+ по 260 ₽ | 2 600 ₽ | 44% |
| 4 × Семейный по 500 ₽ | 2 000 ₽ | 11% |

Для наценки 200% выручка должна быть втрое выше себестоимости: минимум 5 400 ₽
с одного L+ ещё до прочих расходов. Эквивалентные нижние цены при полном
однородном заполнении: 270 ₽ за Личный, 540 ₽ за Личный+ и 1 350 ₽ за Семейный.
Промо 5 € нельзя использовать как постоянную себестоимость.

VPS M+ по обычной цене 9 € заметно лучше для маржи, но перед выбором требуется
нагрузочный тест Xray Reality на 600 Мбит/с и 20 одновременных устройствах.

## Защита персональных credential

Каждая запись устройства получает собственные UUID и порт. Встроенный SSH
transport сначала проверяет кандидатный Xray config, затем атомарно заменяет его
и пересобирает отдельную nftables-таблицу. На входящем и исходящем направлении
для каждого порта действует byte-rate limit 30 Мбит/с. Xray blackhole блокирует
25/465/587 и `geoip:private`. Исторический общий UUID очищается из base inbound
при первой персональной установке; custom transport обязан подтвердить это
флагом `shared_credential_disabled=true`.

Если transport не подтверждает rate limit, SMTP block или отключение общего
UUID, слот освобождается и ссылка пользователю не выдаётся. Диапазон портов
`XRAY_CLIENT_PORT_START..END` должен быть открыт и в firewall provider; guardrail
не разрешает покупку, если диапазон меньше ёмкости узла.

## Healthcheck и Kubernetes

Реализован отдельный worker `python -m src.backend.recovery_agent`. Каждые 15
секунд он получает из общей БД **все** узлы со статусом `active` во всех регионах
и проверяет их параллельно. Для каждого узла используется отдельная DB-сессия и
атомарная lease-блокировка, поэтому отказ одного сервера не останавливает
проверку остальных и два экземпляра агента не отправляют повторные reboot-команды.

Лестница восстановления:

1. TCP-проверка адреса и порта из сохранённой VLESS-ссылки. Первый же промах
   ставит `health_status=down`, поэтому новые устройства на узел не назначаются.
2. После трёх промахов подряд через SSH выполняется
   `systemctl restart xray`, затем повторная проба.
3. Если listener не поднялся, через SSH запрашивается reboot **этого же VPS**.
4. Если SSH недоступен, вызывается настроенный provider reboot adapter с ID
   существующего VPS. Адаптер должен выполнять только reboot, а не purchase или
   replace.
5. После reboot агент ждёт загрузку и повторяет пробы. Если восстановление не
   удалось, узел остаётся `down`, пишется audit и включается cooldown. TCP-пробы
   при этом продолжаются каждый цикл; восстановившийся узел сразу возвращается
   в пул.

Параметры агента:

```dotenv
RECOVERY_PROBE_INTERVAL_SECONDS=15
RECOVERY_MAX_PARALLEL_NODES=32
RECOVERY_PROBE_TIMEOUT_SECONDS=3
RECOVERY_FAILURE_THRESHOLD=3
RECOVERY_RESTART_GRACE_SECONDS=10
RECOVERY_REBOOT_GRACE_SECONDS=45
RECOVERY_REBOOT_PROBE_INTERVAL_SECONDS=10
RECOVERY_REBOOT_PROBE_ATTEMPTS=12
RECOVERY_LOCK_SECONDS=600
RECOVERY_COOLDOWN_SECONDS=300
RECOVERY_SSH_USER=root
RECOVERY_SSH_PRIVATE_KEY_PATH=/etc/emery/recovery-ssh/id_ed25519
RECOVERY_SSH_KNOWN_HOSTS_PATH=/etc/emery/ssh/known_hosts
RECOVERY_ALLOW_UNKNOWN_HOST_KEYS=false
RECOVERY_PROVIDER_REBOOT_SCRIPT=
RECOVERY_HEARTBEAT_FILE=/var/run/emery/recovery-agent.heartbeat
```

Private SSH key сначала берётся из записи конкретного `vpn_nodes`, а для старых
или вручную добавленных серверов — из `RECOVERY_SSH_PRIVATE_KEY_PATH`. В БД и
логах ключ не выводится. Kubernetes Secret `emery-vpn-recovery-ssh` должен
содержать ключ `id_ed25519`, уже добавленный в `authorized_keys` этих серверов.

При автоматическом bootstrap backend повторно входит установленным ключом и
сохраняет публичный SSH host key в записи узла. Для старых узлов host keys можно
передать через `ssh_host_key` admin API или Secret
`emery-vpn-node-known-hosts` должен содержать ключ `known_hosts`, собранный по
доверенному каналу. По умолчанию неизвестные host keys запрещены. Временное
`RECOVERY_ALLOW_UNKNOWN_HOST_KEYS=true` снижает
защиту от MITM и для production не рекомендуется.

`RECOVERY_PROVIDER_REBOOT_SCRIPT`, если задан, запускается с одним аргументом —
JSON вида
`{"action":"reboot_existing_server","node_id":1,"provider_server_id":"...",...}`.
`provider_server_id` — ID именно существующей VM у provider. Нулевой exit
code означает, что hard reboot принят provider. Скрипт не получает VLESS UUID или
SSH private key. Для IONOS его нельзя включать до получения официального reboot
API/разрешения; Kubernetes сам по себе внешний VPS перезагрузить не может.

Перед развёртыванием выполнить `alembic upgrade head`, собрать backend image и
заменить `YOUR_REGISTRY/...:VERSION` в
`deploy/kubernetes/recovery-agent.yaml`. Secret `emery-vpn-backend-env` обязан
содержать тот же `DB_URL`, что backend. Для двух pod не следует использовать два
разных локальных SQLite-файла: production-конфигурация должна использовать общую
внешнюю БД (рекомендуется PostgreSQL).

## Продление без удаления лишнего сервера

Узел не удаляется при временном избытке. Renewal planner сравнивает требуемое
число узлов с уже оплаченными сроками. Если новый VPS создал запас, наиболее
дорогой/старый/нестабильный договор получает `do_not_renew`; новый VPS работает
вместо его следующего продления. До даты окончания старый узел остаётся в пуле
или переводится в резерв. Автоматический delete запрещён.

Поля `contract_id`, `paid_until`, `renewal_price_eur_cents`, `auto_renew` и
`renewal_status` сохраняются в `vpn_nodes`. Предпросмотр доступен через
`GET /api/v1/admin/renewals/plan`, применение — через
`POST /api/v1/admin/renewals/plan/apply`. Планировщик рассматривает только узлы с
нулём назначенных устройств и никогда не уменьшает остаточную ёмкость ниже
`current_clients + 5`.

Фактическое отключение автопродления выполняет только `NODE_RENEWAL_SCRIPT` с
action `disable_auto_renew`; ответ обязан явно вернуть `auto_renew=false`.
`AUTO_RENEWAL_ACTIONS_ENABLED=false` оставляет план в режиме preview. Методы
`deprovision` во всех adapter возвращают `destructive_deprovision_disabled` и
не вызывают удаление VPS.

## Контракт provider-adapter

`NODE_PROVISION_SCRIPT` получает один JSON-аргумент с action
`order_and_bootstrap_server`, стабильным `idempotency_key=emery-node-<id>`,
регионом, 20 местами, 600 Мбит/с и диапазоном персональных портов. Успешный stdout
— JSON, содержащий минимум:

```json
{
  "ok": true,
  "provider_server_id": "...",
  "contract_id": "...",
  "endpoint": "203.0.113.10",
  "config_payload": "vless://...",
  "paid_until": "2026-09-10T00:00:00Z",
  "renewal_price_eur_cents": 500,
  "auto_renew": true,
  "bootstrap_verified": true,
  "credential_transport_ready": true,
  "dedicated_port_range_open": true,
  "rate_limit_ready": true,
  "smtp_block_enforced": true,
  "shared_credential_disabled": true
}
```

Без любого обязательного поля узел остаётся `provision_failed` и никогда не
попадает пользователям. Повтор использует тот же idempotency key. Скрипт не
получает разрешения на delete (`destructive_actions_allowed=false`).
