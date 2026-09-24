"""
coverage.py — генерация пространства двойников с ШИРОКИМ покрытием.

Дефолтный dnstwist покрывает узко. Расширяем тремя способами:
  1) расширенный --tld: и новые зоны, и хостинг-суффиксы (vercel.app и др.);
  2) транслит/кириллица бренда — dnstwist сам не делает, добавляем своим списком;
  3) словарь слов для комбосквоттинга через --dictionary.

build_candidates() возвращает ВЕСЬ сгенерированный список (с DNS-полями), а отсев
до существующих делает уже пайплайн (filter_existing). Так видна воронка:
    сгенерировано (тысячи) → существует (десятки).
"""
import dnstwist, tempfile, os, socket
try:
    import dns.resolver; _HAVE_DNS = True
except ImportError:
    _HAVE_DNS = False

# ---- РАСШИРЕНИЕ ПОКРЫТИЯ (это и есть ваша работа) ----

# Зоны + хостинг-суффиксы одним списком (dnstwist подставит их через --tld).
# >>> ЗАДАНИЕ: двойники живут не только в своих доменах, но и как поддомены на
#     бесплатных площадках (хостинги, динамический DNS). Пример — duckdns.org.
#     Подумайте и НАЙДИТЕ САМИ ещё такие сервисы, впишите их суффиксы сюда —
#     это и есть расширение покрытия. Чем больше площадок, тем шире невод.
EXTRA_TLDS = [
    "live", "shop", "top", "online", "store", "site", "pay", "app",   # зоны
    "duckdns.org",          # пример хостинг-суффикса
    # TODO: допишите свои найденные сервисы (напр. ...)
]

# Транслит бренда — то, чего dnstwist не придумает. НЕОБЯЗАТЕЛЬНО:
# заполните, только если у вашего бренда есть русское имя (напр. Золотое яблоко).
# Для обычного латинского домена оставьте список пустым.
TRANSLIT_BASES = [
    # "zolotoe-yabloko.ru", "zolotoeyabloko.ru",
]

COMBO_WORDS = ["oplata", "bonus", "sale", "official", "support", "opt"]


def _tmp(lines):
    fd, p = tempfile.mkstemp(suffix=".txt", text=True)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return p


def _fuzz(domain, threads=150):
    """Полное пространство пермутаций бренда с DNS (registered=False → всё)."""
    tf, df = _tmp(EXTRA_TLDS), _tmp(COMBO_WORDS)
    try:
        return dnstwist.run(domain=domain, format="null", registered=False,
                            tld=tf, dictionary=df, threads=threads)
    finally:
        os.unlink(tf); os.unlink(df)


def _resolve(dom):
    a, mx = [], []
    try:
        socket.setdefaulttimeout(4); a = [socket.gethostbyname(dom)]
    except Exception:
        pass
    if _HAVE_DNS:
        try:
            for x in dns.resolver.resolve(dom, "MX", lifetime=4):
                mx.append(str(x.exchange).rstrip("."))
        except Exception:
            pass
    return a, mx


def _row(dom, typ, base, a, mx):
    return {"domain": dom, "type": typ, "base": base,
            "dns_a": [x for x in a if x and not x.startswith("!")], "dns_mx": mx}


def build_candidates(brand="goldapple.ru"):
    """Весь список пермутаций с покрытием (существующие и нет)."""
    seen, out = set(), []
    # 1) основной бренд — полный fuzz с DNS
    for r in _fuzz(brand):
        d = r.get("domain")
        if not d or d in seen or r.get("fuzzer") == "*original":
            continue
        seen.add(d)
        out.append(_row(d, r.get("fuzzer"), brand, r.get("dns_a", []), r.get("dns_mx", [])))
    # 2) транслит-базы и их зоны — добавляем свои и резолвим точечно
    for base in TRANSLIT_BASES:
        name = base.rsplit(".", 1)[0]
        for d in [base] + [f"{name}.{t}" for t in EXTRA_TLDS]:
            if d in seen:
                continue
            seen.add(d)
            a, mx = _resolve(d)
            out.append(_row(d, "translit", base, a, mx))
    return out


if __name__ == "__main__":
    import json
    c = build_candidates()
    ex = [x for x in c if x["dns_a"] or x["dns_mx"]]
    print(f"Сгенерировано с покрытием: {len(c)} | существует: {len(ex)}")
    json.dump({"brand": "goldapple.ru", "generated_total": len(c), "candidates": c},
              open("candidates.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
