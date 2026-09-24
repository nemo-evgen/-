Практика 3 (DRP) — Пайплайн двойников: запуск на своём домене

Скрипт почти готов. Ваши задачи — несколько небольших правок, запуск на своём
домене и сдать отчёт со скриншотами. ~15-20 минут.

ФАЙЛЫ
  pipeline.py        — почти готовый пайплайн. Меняете TARGET и дописываете
                       функцию filter_existing().
  coverage.py        — генерация с покрытием. Дописываете свои сервисы-площадки
                       в EXTRA_TLDS.
  urlscan_helper.py  — обвязка urlscan (не нужно менять).
  .env.example       — шаблон для ключа urlscan.

УСТАНОВКА
  pip install dnstwist dnspython urlscan-python python-dotenv

КЛЮЧ urlscan
  urlscan.io -> Settings & API -> создать API key
  cp .env.example .env    и вписать:  URLSCAN_API_KEY=ваш_ключ

ЧТО СДЕЛАТЬ
  1) pipeline.py: TARGET = "ваш-домен"   (реальный известный бренд/сайт!)
  2) pipeline.py: дописать filter_existing() 
  3) coverage.py: в EXTRA_TLDS уже есть пример duckdns.org — НАЙДИТЕ САМИ
                  ещё бесплатные хостинги / динамический DNS и допишите их
  4) python pipeline.py --demo    # проверка без ключа
     python pipeline.py           # боевой прогон -> report.html со скринами

Через urlscan уходит не более 40 доменов (CAP = 40).

СДАТЬ
  - report.html по вашему домену
  - скриншот отчёта (или экрана с результатом)
  - строку вывода из консоли (ВЫВОД: ...)

Если "Существует после фильтра: 0" — либо не дописана filter_existing,
либо домен малоизвестный (возьмите крупный бренд).
Живой двойник смотрим только через песочницу urlscan, не своим браузером.
