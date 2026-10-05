"""Read-only derivation of candidate school site groups. No writes."""
import asyncio
import json
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session_maker
from app.models import School, SourcePage
from app.models.scrape_log import ScrapeType
from app.scrapers.extractor_helpers import transliterate_bulgarian
from app.services.identity_adjudication import _registrable_domain
from app.services.school_relations import brand, shared_brand_key
from app.services.school_relations import brand_key as key
from app.utils.i18n_resolver import resolve_display_name_i18n


def addr_key(value):
    text = re.sub(r'(?i)\b(гр|с|ж\.?к|кв|бул|ул|район|№)\b\.?', ' ', str(value or ''))
    return re.sub(r'[^а-яa-z0-9]+', '', text.casefold())[:28]

def presentation_hit(brand_text, corpus):
    """Does the shared site use this entity's registry brand, and how often?

    A site that brands several entities as one school names each of them. A site
    that never uses an entity's registry brand is publishing a different identity
    for it — either because the entity is a legal shell (school 150, whose name
    appears nowhere on its own site) or because the school brands itself in
    another language (`Maple Bear` for `Канадско мече`).

    Matches contiguous multi-word phrases from the brand, in the original script
    and transliterated, and falls back to the single word only when the brand is
    one word. Bare common words are deliberately excluded: `програмиране` occurs
    97 times on a programming school's site without ever naming the entity.
    """
    words = [w for w in re.split(r'\s+', brand_text) if len(w) > 2]
    if not words:
        return {'phrase': None, 'count': 0}

    phrases = [' '.join(words)] if len(words) > 1 else list(words)
    for size in (3, 2):
        for start in range(len(words) - size + 1):
            phrases.append(' '.join(words[start:start + size]))

    best = {'phrase': None, 'count': 0}
    for phrase in phrases:
        for form in {phrase, transliterate_bulgarian(phrase)}:
            needle = form.casefold().strip()
            if len(needle) < 4:
                continue
            count = corpus.count(needle)
            if count > best['count']:
                best = {'phrase': needle, 'count': count}
    return best


async def main():
    async with async_session_maker() as db:
        schools = (await db.execute(
            select(School).options(selectinload(School.locations))
        )).scalars().all()
        shared_ids = [s.id for s in schools]
        pages = (await db.execute(
            select(SourcePage.school_id, SourcePage.raw_markdown).where(
                SourcePage.school_id.in_(shared_ids),
                SourcePage.scrape_type == ScrapeType.WEBSITE,
                SourcePage.is_valid.is_(True),
                SourcePage.raw_markdown.isnot(None),
            )
        )).all()
    corpus_by_school = defaultdict(list)
    for school_id, markdown in pages:
        corpus_by_school[school_id].append((markdown or '')[:12000])
    priv = [s for s in schools if s.school_type in ('private', 'international') and s.website_url]
    by_domain = defaultdict(list)
    for s in priv:
        by_domain[_registrable_domain(s.website_url)].append(s)

    groups = []
    for domain, members in sorted({d: m for d, m in by_domain.items() if len(m) > 1}.items()):
        corpus = ' '.join(
            chunk for s in members for chunk in corpus_by_school.get(s.id, [])
        ).casefold()
        rows = []
        for s in sorted(members, key=lambda x: x.id):
            addr = next(((l.address_i18n or {}).get('bg') for l in s.locations
                         if (l.address_i18n or {}).get('bg')), '')
            rows.append({
                'id': s.id,
                'level': s.education_level,
                'registry': re.sub(r'\s+', ' ', str((s.name_i18n or {}).get('bg') or '')).strip(' "'),
                'brand': brand((s.name_i18n or {}).get('bg')),
                'address': re.sub(r'\s+', ' ', addr).strip(),
                'published_en': (resolve_display_name_i18n(s.attributes or {}) or {}).get('en'),
                'url': s.website_url,
                'presented_as': presentation_hit(brand((s.name_i18n or {}).get('bg')), corpus),
            })
        shared_brand = shared_brand_key(key(r['brand']) for r in rows)
        addrs = {addr_key(r['address']) for r in rows if r['address']}
        levels = [r['level'] for r in rows]
        if shared_brand and len(set(levels)) == len(levels):
            kind = 'level_split'
        elif shared_brand:
            kind = 'campus_branch'
        else:
            kind = 'review_required'
        groups.append({
            'domain': domain,
            'kind': kind,
            'shared_brand': bool(shared_brand),
            'same_address': len(addrs) == 1,
            'distinct_levels': len(set(levels)) == len(levels),
            'published_en': sorted({r['published_en'] for r in rows if r['published_en']}),
            'members': rows,
        })
    print(json.dumps(groups, ensure_ascii=False, indent=1))

asyncio.run(main())
