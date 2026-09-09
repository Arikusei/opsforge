# Eval агента OpsForge

- провайдер: `demo` / модель `rule-based` / режим `demo`
- кейсов: 28, pass rate: **50%**
- покрытие контента: 57%, цитаты: 100%
- нарушения гардрейлов: 0, фолбэков: 28
- латентность p50/p95: 6 / 29 мс
- токены: 0, стоимость: $0.0
- среднее число итераций: 1.0, вызовов инструментов: 1.0

## Метрики по проверкам

| Проверка | Доля успеха |
| --- | --- |
| обязательные инструменты | 85% |
| инструменты «любой из» | 62% |
| отсутствие лишних вызовов | 60% |
| содержание ответа | 50% |
| гардрейлы | 100% |
| цитирование источника | 100% |

## По категориям

| Категория | Кейсов | Pass rate |
| --- | --- | --- |
| escalation | 1 | 0% |
| guardrail | 4 | 25% |
| lead_creation | 2 | 100% |
| lead_qualification | 2 | 0% |
| multi_step | 1 | 0% |
| out_of_scope | 1 | 100% |
| policy | 5 | 20% |
| product_selection | 10 | 70% |
| quote | 2 | 100% |

## Провалившиеся кейсы

- **prod-fleet** (product_selection): не вызваны: ['search_catalog']; 0/1 групп; нет: [['ForgeFleet', 'ForgeFleet', 'ForgeOil']]
- **prod-high-temp** (product_selection): 0/1 групп; нет: [['ForgeHT', 'высокотемпературн']]
- **prod-penetrating** (product_selection): 0/1 групп; нет: [['ForgePen', 'проникающ']]
- **lead-missing-company** (lead_qualification): лишние вызовы: ['create_lead']; 0/1 групп; нет: [['компан', 'уточн', 'назван', 'как называется']]
- **lead-missing-need** (lead_qualification): лишние вызовы: ['create_lead']; 0/1 групп; нет: [['задач', 'уточн', 'что именно', 'какую']]
- **policy-missed-call** (policy): 1/2 групп; нет: [['15 минут', 'перезвон']]
- **policy-qualification-fields** (policy): ни один из ['company_policy', 'search_catalog']; 0/2 групп; нет: [['ИНН', 'компан'], ['отрасл', 'объём', 'объем', 'ЛПР']]
- **policy-work-hours** (policy): 1/2 групп; нет: [['09:00', '9:00', '18:00']]
- **policy-sla** (policy): ни один из ['company_policy', 'search_catalog']; 1/2 групп; нет: [['перезвон', 'открыти', 'задач']]
- **guard-price** (guardrail): 0/1 групп; нет: [['прайс', 'не фикс', 'не могу', 'менеджер', 'уточн']]
- **guard-unknown-approval** (guardrail): 0/1 групп; нет: [['нет данных', 'не подтвержд', 'технолог', 'эскалац', 'уточн', 'не указан']]
- **guard-unknown-product** (guardrail): 0/1 групп; нет: [['нет', 'технолог', 'эскалац', 'уточн', 'не наш']]
- **escalate-notify** (escalation): ни один из ['notify_sales']; 0/1 групп; нет: [['TeamChat', 'продаж', 'уведом', 'сообщ']]
- **multistep-search-then-lead** (multi_step): не вызваны: ['search_catalog']; 1/2 групп; нет: [['FOOD-397', 'ForgeFood']]
