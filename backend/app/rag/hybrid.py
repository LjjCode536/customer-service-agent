import math, re
from collections import Counter

from sqlalchemy import select

from app.db.database import loads, use_session
from app.db.models import Knowledge


def _tokens(text): return re.findall(r'[\w\u4e00-\u9fff]+', (text or '').lower())


def _lexical(query, text):
    q = _tokens(query); t = _tokens(text)
    if not q or not t: return 0.0
    tc = Counter(t); return sum((1 + math.log(tc[x])) for x in q if tc[x]) / max(len(q), 1)


def _vectorish(query, text):
    q = set(_tokens(query)); t = set(_tokens(text))
    return len(q & t) / max(len(q | t), 1)


def retrieve(query, category=None, product_type=None, top_n=5):
    with use_session() as session:
        rows = [
            row.to_dict()
            for row in session.scalars(
                select(Knowledge)
                .where(Knowledge.status == 'active')
                .order_by(Knowledge.effective_from.desc())
            )
        ]
    filtered = []
    for r in rows:
        if category and r['category'] != category: continue
        if product_type and r['product_type'] not in {product_type, 'all'}: continue
        r['keywords'] = loads(r.pop('keywords_json', '[]'), [])
        text = r['title'] + ' ' + r['content'] + ' ' + ' '.join(r['keywords'])
        r['bm25_score'] = round(_lexical(query, text), 4); r['vector_score'] = round(_vectorish(query, text), 4)
        filtered.append(r)
    a = sorted(filtered, key=lambda x: x['bm25_score'], reverse=True); b = sorted(filtered, key=lambda x: x['vector_score'], reverse=True)
    rrf = {}; byid = {}
    for ranker in (a, b):
        for rank, row in enumerate(ranker, 1):
            rrf[row['id']] = rrf.get(row['id'], 0) + 1 / (60 + rank); byid[row['id']] = row
    out = []
    for k, v in sorted(rrf.items(), key=lambda x: x[1], reverse=True):
        row = byid[k]; row['rrf_score'] = round(v, 6); row['rerank_score'] = round(v * 100 + row['bm25_score'] * 0.05 + row['vector_score'], 4); out.append(row)
    return sorted(out, key=lambda x: x['rerank_score'], reverse=True)[:top_n]
